from flask import Flask, render_template, request, send_file
import os
from dotenv import load_dotenv
import pymysql
import pandas as pd
from io import BytesIO
from datetime import datetime

load_dotenv()
app = Flask(__name__)

# Conexión a MySQL
def get_db_connection():
    return pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    ssl={'ssl': {'ca': '/etc/ssl/cert.pem'}}
)

# Obtener valores únicos para filtros
def obtener_valores_unicos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT CIUDAD FROM baseprueba ORDER BY CIUDAD")
    ciudades = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT COMPANIA FROM baseprueba ORDER BY COMPANIA")
    companias = [row[0] for row in cursor.fetchall() if row[0]]

    cursor.execute("SELECT DISTINCT fecha_utilizacion FROM historial_descargas ORDER BY fecha_utilizacion DESC")
    fechas = [row[0].strftime("%Y-%m-%d") for row in cursor.fetchall() if row[0] is not None]

    conn.close()
    return ciudades, companias, fechas

# Aplicar filtros
def aplicar_filtros(filters, limitar=True):
    condiciones = ["1=1"]
    params = []

    if filters.get('lineas_exactas'):
        condiciones.append("Q_LINEAS = %s")
        params.append(filters['lineas_exactas'])
    else:
        if filters.get('min_lineas'):
            condiciones.append("Q_LINEAS >= %s")
            params.append(filters['min_lineas'])
        if filters.get('max_lineas'):
            condiciones.append("Q_LINEAS <= %s")
            params.append(filters['max_lineas'])

    if filters.get('cargo_fijo_min'):
        condiciones.append("CARGO_FIJO_POR_LINEA >= %s")
        params.append(filters['cargo_fijo_min'])

    if filters.get('bloqueado_mail') == 'si':
        condiciones.append("MAIL IN (SELECT CORREO FROM correos_bloqueados)")
    elif filters.get('bloqueado_mail') == 'no':
        condiciones.append("MAIL NOT IN (SELECT CORREO FROM correos_bloqueados)")

    if filters.get('inicio_actividad') == 'si':
        condiciones.append("RUT IN (SELECT rut FROM inicio_actividades WHERE estado = 'SI')")
    elif filters.get('inicio_actividad') == 'no':
        condiciones.append("RUT IN (SELECT rut FROM inicio_actividades WHERE estado = 'NO')")
    elif filters.get('inicio_actividad') == 'pendiente':
        condiciones.append("RUT NOT IN (SELECT rut FROM inicio_actividades)")

    if filters.get('ciudades'):
        condiciones.append("CIUDAD IN (%s)" % ",".join(["%s"] * len(filters['ciudades'])))
        params.extend(filters['ciudades'])

    if filters.get('companias'):
        condiciones.append("COMPANIA IN (%s)" % ",".join(["%s"] * len(filters['companias'])))
        params.extend(filters['companias'])

    if filters.get('dias_sin_uso'):
        condiciones.append("RUT NOT IN (SELECT rut FROM historial_descargas WHERE DATEDIFF(CURDATE(), fecha_utilizacion) <= %s)")
        params.append(filters['dias_sin_uso'])

    if filters.get('motivo_utilizacion'):
        condiciones.append("RUT NOT IN (SELECT rut FROM historial_descargas WHERE motivo IN (%s))" % ",".join(["%s"] * len(filters['motivo_utilizacion'])))
        params.extend(filters['motivo_utilizacion'])

    if filters.get('fecha_utilizacion'):
        fechas = filters['fecha_utilizacion']
        condiciones.append("(RUT IN (SELECT rut FROM historial_descargas WHERE fecha_utilizacion IN (%s)))" % ",".join(["%s"] * len(fechas)))
        params.extend(fechas)

    # Filtro para correos de Gmail
    if filters.get('gmail_filter') == 'gmail':
        condiciones.append("MAIL LIKE '%@gmail.com%'")
        params.append('%@gmail.com%')
    elif filters.get('gmail_filter') == 'nogmail':
        condiciones.append("MAIL NOT LIKE '%@gmail.com%'")
        params.append('%@gmail.com%')

    condiciones.append("RUT NOT IN (SELECT rut FROM clientes_vendidos)")

    where_clause = " AND ".join(condiciones)
    query = f"SELECT * FROM baseprueba WHERE {where_clause}"
    if limitar:
        query += " LIMIT 20"

    conn = get_db_connection()
    df = pd.read_sql(query, conn, params=params)

    if not limitar:
        total_lineas = df['NUMERO_MOVIL'].nunique()
        total_clientes = df['RUT'].nunique()
    else:
        # para obtener los totales globales (aunque en pantalla se muestre limit 20)
        total_query = f"SELECT NUMERO_MOVIL, RUT FROM baseprueba WHERE {where_clause}"
        df_total = pd.read_sql(total_query, conn, params=params)
        total_lineas = df_total['NUMERO_MOVIL'].nunique()
        total_clientes = df_total['RUT'].nunique()

    conn.close()
    return df, total_lineas, total_clientes

@app.route('/', methods=['GET', 'POST'])
def index():
    filters = {
        'min_lineas': request.form.get('min_lineas'),
        'max_lineas': request.form.get('max_lineas'),
        'lineas_exactas': request.form.get('lineas_exactas'),
        'cargo_fijo_min': request.form.get('cargo_fijo_min'),
        'bloqueado_mail': request.form.get('bloqueado_mail'),
        'inicio_actividad': request.form.get('inicio_actividad'),
        'ciudades': request.form.getlist('ciudad'),
        'companias': request.form.getlist('compania'),
        'dias_sin_uso': request.form.get('dias_sin_uso'),
        'motivo_utilizacion': request.form.getlist('motivo_utilizacion'),
        'fecha_utilizacion': request.form.getlist('fecha_utilizacion'),
        'gmail_filter': request.form.get('gmail_filter')
    }

    ciudades, companias, fechas = obtener_valores_unicos()
    data, total_lineas, total_clientes = [], 0, 0

    if request.method == 'POST':
        df, total_lineas, total_clientes = aplicar_filtros(filters, limitar=True)
        data = df.to_dict('records') if not df.empty else []

    return render_template('index.html', data=data, ciudades=ciudades, companias=companias,
                           fechas_utilizadas=fechas, filters=filters,
                           total_lineas=total_lineas, total_clientes=total_clientes)

@app.route('/exportar', methods=['POST'])
def exportar():
    filters = {
        'min_lineas': request.form.get('min_lineas'),
        'max_lineas': request.form.get('max_lineas'),
        'lineas_exactas': request.form.get('lineas_exactas'),
        'cargo_fijo_min': request.form.get('cargo_fijo_min'),
        'bloqueado_mail': request.form.get('bloqueado_mail'),
        'inicio_actividad': request.form.get('inicio_actividad'),
        'ciudades': request.form.getlist('ciudad'),
        'companias': request.form.getlist('compania'),
        'dias_sin_uso': request.form.get('dias_sin_uso'),
        'motivo_utilizacion': request.form.getlist('motivo_utilizacion'),
        'fecha_utilizacion': request.form.getlist('fecha_utilizacion'),
        'gmail_filter': request.form.get('gmail_filter')
    }

    motivo = request.form.get('motivo')
    if not motivo:
        return "Debe seleccionar un motivo de descarga", 400

    df, _, _ = aplicar_filtros(filters, limitar=False)

    conn = get_db_connection()
    cursor = conn.cursor()
    fecha_actual = datetime.now().strftime("%Y-%m-%d")
    for rut in df['RUT'].unique():
        cursor.execute("INSERT INTO historial_descargas (rut, fecha_utilizacion, motivo) VALUES (%s, %s, %s)",
                       (rut, fecha_actual, motivo))
    conn.commit()
    conn.close()

    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if len(df) > 1000000:
            for i in range(0, len(df), 1000000):
                df.iloc[i:i+1000000].to_excel(writer, index=False, sheet_name=f"Datos_{i//1000000+1}")
        else:
            df.to_excel(writer, index=False, sheet_name="Datos")

    output.seek(0)
    return send_file(
        output,
        download_name="clientes_filtrados.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)


