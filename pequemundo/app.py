from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from sqlalchemy import text
import mercadopago
import json

app = Flask(__name__)
app.secret_key = 'pequemundo_secret_2024'

# =========================
# CONFIGURACIÓN BASE DE DATOS
# Parámetros de pool para Railway: reconexión automática y resistencia a timeouts
# =========================
app.config['SQLALCHEMY_DATABASE_URI'] = (
    "mysql+pymysql://root:sWzAyVoEzgCxHyaqWzkgbkRodAAEnJmM"
    "@zephyr.proxy.rlwy.net:39579/railway"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,        # Verifica conexión antes de usarla
    'pool_recycle': 280,          # Recicla conexiones cada ~4 min (Railway cierra a los 5)
    'pool_size': 5,
    'max_overflow': 10,
    'connect_args': {
        'connect_timeout': 10,
    }
}

db = SQLAlchemy(app)


@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return []


# =========================
# MERCADO PAGO
# =========================
MP_ACCESS_TOKEN = "TEST-7405494608123272-053123-046e81571c23bf9c73efb64662b94c28-585535158"
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


# URL base pública — se detecta automáticamente en Railway.
# Railway inyecta RAILWAY_PUBLIC_DOMAIN con tu dominio de deploy.
# También puedes setear MP_BASE_URL manualmente como variable de entorno
# (útil en local con ngrok: export MP_BASE_URL=https://xxxx.ngrok-free.app).
_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
_mp_base_env    = os.environ.get("MP_BASE_URL", "")

if _mp_base_env:
    MP_BASE_URL = _mp_base_env.rstrip("/")
elif _railway_domain:
    MP_BASE_URL = f"https://{_railway_domain}"
else:
    MP_BASE_URL = ""  # sin URL pública: MP no funcionará en local sin ngrok

print(f"[MP] BASE URL: {MP_BASE_URL or '⚠️  NO CONFIGURADA'}")


# =========================
# MODELOS
# =========================
class Producto(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    nombre    = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(300), nullable=False)
    imagen    = db.Column(db.String(255), nullable=False)
    categoria = db.Column(db.String(50), nullable=False)
    precio    = db.Column(db.Float, nullable=False)
    stock     = db.Column(db.Integer, nullable=False)
    estado    = db.Column(db.String(20), nullable=False)


class Usuario(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    nombre   = db.Column(db.String(100), nullable=False)
    email    = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    telefono = db.Column(db.String(20), nullable=True)
    rol      = db.Column(db.String(20), nullable=False, default='Cliente')


class Pedido(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    cliente          = db.Column(db.String(100), nullable=False)
    cliente_email    = db.Column(db.String(100), nullable=True)
    total            = db.Column(db.Float, nullable=False)
    estado           = db.Column(db.String(30), nullable=False, default='Pendiente')
    vendedor_id      = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    mp_preference_id = db.Column(db.String(200), nullable=True)
    mp_payment_id    = db.Column(db.String(200), nullable=True)
    mp_status        = db.Column(db.String(50), nullable=True)
    items_json       = db.Column(db.Text, nullable=True)
    vendedor         = db.relationship('Usuario', backref='pedidos_vendedor', foreign_keys=[vendedor_id])


# =========================
# DECORADORES
# =========================
def requiere_login(f):
    """Cualquier usuario autenticado."""
    @wraps(f)
    def decorado(*args, **kwargs):
        if not session.get('usuario_id'):
            flash('Debes iniciar sesión.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorado


def requiere_admin(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if session.get('rol') != 'Admin':
            flash('Debes iniciar sesión como administrador.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorado


def requiere_vendedor(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if session.get('rol') not in ('Vendedor', 'Admin'):
            flash('Debes iniciar sesión como vendedor.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorado


# =========================
# UTILIDAD MP
# Construye la preferencia con URLs dinámicas de la app
# =========================
def crear_preferencia_mp(items_list, cliente_nombre, cliente_email, pedido_id):
    if not MP_BASE_URL:
        raise ValueError(
            "MP_BASE_URL no está configurada. "
            "Setea la variable de entorno MP_BASE_URL con tu URL pública "
            "(ngrok en local, o el dominio de producción)."
        )

    mp_items = [{
        "id":          str(i['id']),
        "title":       i['nombre'],
        "quantity":    int(i['cantidad']),
        "unit_price":  float(i['precio']),
        "currency_id": "CLP"
    } for i in items_list]

    preference_data = {
        "items": mp_items,
        "payer": {
            "name":  cliente_nombre,
            "email": cliente_email or "test_user@test.com"
        },
        "back_urls": {
            "success": f"{MP_BASE_URL}/mp/success",
            "failure": f"{MP_BASE_URL}/mp/failure",
            "pending": f"{MP_BASE_URL}/mp/pending",
        },
        "auto_return":        "approved",
        "external_reference": str(pedido_id),
        "notification_url":   f"{MP_BASE_URL}/mp/webhook",
    }

    print("=== CREANDO PREFERENCIA MP ===")
    print("BASE URL :", MP_BASE_URL)
    print("SUCCESS  :", preference_data["back_urls"]["success"])
    print("FAILURE  :", preference_data["back_urls"]["failure"])
    print("PENDING  :", preference_data["back_urls"]["pending"])
    print("WEBHOOK  :", preference_data["notification_url"])

    response = sdk.preference().create(preference_data)
    return response.get("response", {})


# =========================
# DIAGNÓSTICO
# =========================
@app.route('/test-db')
def test_db():
    try:
        db.session.execute(text("SELECT 1"))
        return "MySQL OK ✅"
    except Exception as e:
        return f"Error BD: {e}", 500


# =========================
# PÁGINAS PÚBLICAS
# =========================
@app.route('/')
def inicio():
    # Pasar los primeros 3 productos activos para la sección de ofertas en index.html
    productos = Producto.query.filter_by(estado='Activo').limit(3).all()
    return render_template('index.html', productos=productos)


@app.route('/catalogo')
def catalogo():
    categoria = request.args.get('categoria', None)
    if categoria:
        productos = Producto.query.filter_by(categoria=categoria, estado='Activo').all()
    else:
        productos = Producto.query.filter_by(estado='Activo').all()
    return render_template('catalogo.html', productos=productos)


# =========================
# AUTENTICACIÓN
# =========================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        usuario  = Usuario.query.filter_by(email=email).first()
        if usuario and usuario.password == password:
            session['usuario_id'] = usuario.id
            session['usuario']    = usuario.nombre
            session['rol']        = usuario.rol
            flash(f'¡Bienvenido, {usuario.nombre}!', 'success')
            if usuario.rol == 'Admin':
                return redirect(url_for('dashboard'))
            elif usuario.rol == 'Vendedor':
                return redirect(url_for('vendedor_dashboard'))
            else:
                return redirect(url_for('inicio'))
        else:
            flash('Correo o contraseña incorrectos.', 'danger')
    return render_template('login.html')


@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre    = request.form['nombre']
        email     = request.form['email']
        telefono  = request.form.get('telefono', '')
        password  = request.form['password']
        confirmar = request.form['confirmar_password']
        if password != confirmar:
            flash('Las contraseñas no coinciden.', 'danger')
            return render_template('registro.html')
        if Usuario.query.filter_by(email=email).first():
            flash('Ya existe una cuenta con ese correo.', 'danger')
            return render_template('registro.html')
        nuevo = Usuario(nombre=nombre, email=email, telefono=telefono,
                        password=password, rol='Cliente')
        db.session.add(nuevo)
        db.session.commit()
        flash('Cuenta creada exitosamente. Inicia sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('registro.html')


@app.route('/cerrar_sesion')
def cerrar_sesion():
    session.clear()
    flash('Sesión cerrada.', 'info')
    return redirect(url_for('inicio'))


# =========================
# CARRITO — CHECKOUT
# Disponible para todos los roles autenticados (Cliente, Vendedor, Admin)
# =========================
@app.route('/carrito/checkout', methods=['POST'])
@requiere_login
def carrito_checkout():
    data  = request.get_json(silent=True) or {}
    items = data.get('items', [])

    if not items:
        return jsonify({'error': 'Carrito vacío'}), 400

    usuario = Usuario.query.get(session['usuario_id'])
    if not usuario:
        return jsonify({'error': 'Usuario no encontrado'}), 401

    # Validar stock antes de crear el pedido
    for item in items:
        producto = Producto.query.get(item['id'])
        if not producto or producto.estado != 'Activo':
            return jsonify({'error': f"Producto '{item['nombre']}' no disponible"}), 400
        if producto.stock < item['cantidad']:
            return jsonify({'error': f"Stock insuficiente para '{item['nombre']}' (disponible: {producto.stock})"}), 400

    total = sum(i['precio'] * i['cantidad'] for i in items)

    nuevo_pedido = Pedido(
        cliente       = usuario.nombre,
        cliente_email = usuario.email,
        total         = total,
        estado        = 'Pendiente',
        vendedor_id   = None,   # compra propia desde carrito, no asignada a vendedor
        items_json    = json.dumps(items)
    )
    db.session.add(nuevo_pedido)
    db.session.commit()

    try:
        preference = crear_preferencia_mp(items, usuario.nombre, usuario.email, nuevo_pedido.id)

        print("=== RESPUESTA MERCADO PAGO ===")
        print(preference)

        if preference.get("id"):
            nuevo_pedido.mp_preference_id = preference["id"]
            db.session.commit()

            checkout_url = (
                preference.get("sandbox_init_point")
                or preference.get("init_point")
            )
            print("CHECKOUT URL:", checkout_url)
            return jsonify({'checkout_url': checkout_url})

        # MP respondió pero sin ID — eliminar pedido huérfano
        print("ERROR: MP no devolvió ID:", preference)
        db.session.delete(nuevo_pedido)
        db.session.commit()
        return jsonify({'error': 'Mercado Pago no respondió correctamente', 'detalle': str(preference)}), 500

    except Exception as e:
        print("ERROR MERCADO PAGO:", str(e))
        db.session.delete(nuevo_pedido)
        db.session.commit()
        msg = str(e)
        if "MP_BASE_URL" in msg:
            msg = "MP_BASE_URL no configurada. Lee las instrucciones en app.py."
        return jsonify({'error': msg}), 500


# =========================
# MIS PEDIDOS
# Accesible para cualquier usuario autenticado (Cliente, Vendedor, Admin)
# Filtra por email para que cada usuario solo vea los suyos
# =========================
@app.route('/mis-pedidos')
@requiere_login
def mis_pedidos():
    usuario = Usuario.query.get(session['usuario_id'])
    pedidos = Pedido.query.filter_by(
        cliente_email=usuario.email
    ).order_by(Pedido.id.desc()).all()
    return render_template('mis_pedidos.html', pedidos=pedidos)


# =========================
# PANEL ADMIN
# =========================
@app.route('/admin')
@requiere_admin
def dashboard():
    productos = Producto.query.all()
    pedidos   = Pedido.query.all()
    usuarios  = Usuario.query.all()
    return render_template('dashboard.html', productos=productos, pedidos=pedidos, usuarios=usuarios)


@app.route('/admin/panel')
@requiere_admin
def admin_dashboard():
    return redirect(url_for('dashboard'))


@app.route('/admin/productos')
@requiere_admin
def productos():
    productos = Producto.query.all()
    return render_template('productos.html', productos=productos)


@app.route('/admin/agregar', methods=['GET', 'POST'])
@requiere_admin
def agregar_producto():
    if request.method == 'POST':
        nuevo = Producto(
            nombre      = request.form['nombre'],
            descripcion = request.form['descripcion'],
            imagen      = request.form['imagen'],
            categoria   = request.form['categoria'],
            precio      = float(request.form['precio']),
            stock       = int(request.form['stock']),
            estado      = request.form['estado']
        )
        db.session.add(nuevo)
        db.session.commit()
        flash('Producto agregado correctamente.', 'success')
        return redirect(url_for('productos'))
    return render_template('agregar_producto.html')


@app.route('/admin/editar/<int:id>', methods=['GET', 'POST'])
@requiere_admin
def editar_producto(id):
    producto = Producto.query.get_or_404(id)
    if request.method == 'POST':
        producto.nombre      = request.form['nombre']
        producto.descripcion = request.form['descripcion']
        producto.imagen      = request.form['imagen']
        producto.categoria   = request.form['categoria']
        producto.precio      = float(request.form['precio'])
        producto.stock       = int(request.form['stock'])
        producto.estado      = request.form['estado']
        db.session.commit()
        flash('Producto actualizado.', 'success')
        return redirect(url_for('productos'))
    return render_template('editar_producto.html', producto=producto)


@app.route('/admin/eliminar/<int:id>', methods=['POST'])
@requiere_admin
def eliminar_producto(id):
    producto = Producto.query.get_or_404(id)
    db.session.delete(producto)
    db.session.commit()
    flash('Producto eliminado.', 'success')
    return redirect(url_for('productos'))


@app.route('/admin/pedidos')
@requiere_admin
def pedidos():
    pedidos = Pedido.query.order_by(Pedido.id.desc()).all()
    return render_template('pedidos.html', pedidos=pedidos)


@app.route('/admin/usuarios')
@requiere_admin
def usuarios():
    usuarios = Usuario.query.all()
    return render_template('usuarios.html', usuarios=usuarios)


@app.route('/admin/actualizar_rol/<int:id>', methods=['POST'])
@requiere_admin
def actualizar_rol(id):
    usuario      = Usuario.query.get_or_404(id)
    usuario.rol  = request.form['rol']
    db.session.commit()
    flash('Rol actualizado.', 'success')
    return redirect(url_for('usuarios'))


# =========================
# PANEL VENDEDOR
# =========================
@app.route('/vendedor')
@requiere_vendedor
def vendedor_dashboard():
    vendedor_id    = session.get('usuario_id')
    mis_pedidos_v  = Pedido.query.filter_by(vendedor_id=vendedor_id).all()
    productos      = Producto.query.filter_by(estado='Activo').all()
    total_ventas   = sum(p.total for p in mis_pedidos_v if p.mp_status == 'approved')
    pedidos_pendientes = sum(1 for p in mis_pedidos_v if p.estado == 'Pendiente')
    pedidos_pagados    = sum(1 for p in mis_pedidos_v if p.mp_status == 'approved')
    return render_template('vendedor_dashboard.html',
                           mis_pedidos=mis_pedidos_v,
                           productos=productos,
                           total_ventas=total_ventas,
                           pedidos_pendientes=pedidos_pendientes,
                           pedidos_pagados=pedidos_pagados)


@app.route('/vendedor/pedidos')
@requiere_vendedor
def vendedor_pedidos():
    vendedor_id = session.get('usuario_id')
    pedidos     = Pedido.query.filter_by(vendedor_id=vendedor_id).order_by(Pedido.id.desc()).all()
    return render_template('vendedor_pedidos.html', pedidos=pedidos)


@app.route('/vendedor/catalogo')
@requiere_vendedor
def vendedor_catalogo():
    productos = Producto.query.filter_by(estado='Activo').all()
    return render_template('vendedor_catalogo.html', productos=productos)


@app.route('/vendedor/estadisticas')
@requiere_vendedor
def vendedor_estadisticas():
    vendedor_id = session.get('usuario_id')
    pedidos     = Pedido.query.filter_by(vendedor_id=vendedor_id).all()
    aprobados   = [p for p in pedidos if p.mp_status == 'approved']
    pendientes  = [p for p in pedidos if p.estado == 'Pendiente' and p.mp_status != 'approved']
    rechazados  = [p for p in pedidos if p.mp_status == 'rejected']
    total_ventas = sum(p.total for p in aprobados)
    return render_template('vendedor_estadisticas.html',
                           pedidos=pedidos, aprobados=aprobados,
                           pendientes=pendientes, rechazados=rechazados,
                           total_ventas=total_ventas)


@app.route('/vendedor/crear_pedido', methods=['GET', 'POST'])
@requiere_vendedor
def vendedor_crear_pedido():
    productos = Producto.query.filter(Producto.estado == 'Activo', Producto.stock > 0).all()

    if request.method == 'POST':
        cliente       = request.form['cliente']
        cliente_email = request.form['cliente_email']
        items_raw     = request.form.get('items', '[]')

        try:
            items = json.loads(items_raw)
        except Exception:
            flash('Error en los productos seleccionados.', 'danger')
            return render_template('vendedor_crear_pedido.html', productos=productos)

        if not items:
            flash('Debes agregar al menos un producto.', 'danger')
            return render_template('vendedor_crear_pedido.html', productos=productos)

        # Validar stock
        for item in items:
            prod = Producto.query.get(item['id'])
            if not prod or prod.stock < item['cantidad']:
                flash(f"Stock insuficiente para '{item['nombre']}'.", 'danger')
                return render_template('vendedor_crear_pedido.html', productos=productos)

        total = sum(i['precio'] * i['cantidad'] for i in items)

        nuevo_pedido = Pedido(
            cliente       = cliente,
            cliente_email = cliente_email,
            total         = total,
            estado        = 'Pendiente',
            vendedor_id   = session.get('usuario_id'),
            items_json    = items_raw
        )
        db.session.add(nuevo_pedido)
        db.session.commit()

        try:
            preference = crear_preferencia_mp(items, cliente, cliente_email, nuevo_pedido.id)
            if preference.get("id"):
                nuevo_pedido.mp_preference_id = preference["id"]
                db.session.commit()
                checkout_url = preference.get("sandbox_init_point") or preference.get("init_point")
                return redirect(checkout_url)
            flash('Pedido creado pero Mercado Pago no respondió correctamente.', 'warning')
        except Exception as e:
            flash(f'Error Mercado Pago: {e}', 'danger')

        return redirect(url_for('vendedor_pedidos'))

    return render_template('vendedor_crear_pedido.html', productos=productos)


# =========================
# MERCADO PAGO — CALLBACKS
# =========================
def _redirect_post_pago():
    """Redirige al lugar correcto según el rol del usuario."""
    rol = session.get('rol')
    if rol == 'Admin':
        return redirect(url_for('mis_pedidos'))
    if rol == 'Vendedor':
        return redirect(url_for('vendedor_pedidos'))
    return redirect(url_for('mis_pedidos'))


def _actualizar_stock(pedido):
    """Descuenta stock al confirmar pago. Llama solo en estado 'approved'."""
    if not pedido.items_json:
        return
    try:
        for item in json.loads(pedido.items_json):
            prod = Producto.query.get(item['id'])
            if prod:
                prod.stock = max(0, prod.stock - item['cantidad'])
                if prod.stock == 0:
                    prod.estado = 'Agotado'
    except Exception as e:
        print("Error actualizando stock:", e)


@app.route('/mp/success')
def mp_success():
    payment_id   = request.args.get('payment_id')
    external_ref = request.args.get('external_reference')
    status       = request.args.get('status')

    if external_ref:
        pedido = Pedido.query.get(int(external_ref))
        if pedido:
            pedido.mp_payment_id = payment_id
            pedido.mp_status     = status
            if status == 'approved':
                pedido.estado = 'Pagado'
                _actualizar_stock(pedido)
            db.session.commit()

    flash('¡Pago realizado con éxito! 🎉', 'success')
    return _redirect_post_pago()


@app.route('/mp/failure')
def mp_failure():
    external_ref = request.args.get('external_reference')

    if external_ref:
        pedido = Pedido.query.get(int(external_ref))
        if pedido:
            pedido.mp_status = 'rejected'
            pedido.estado    = 'Rechazado'
            db.session.commit()

    flash('El pago fue rechazado. Puedes intentarlo nuevamente.', 'danger')
    rol = session.get('rol')
    if rol in ('Vendedor', 'Admin'):
        return redirect(url_for('mis_pedidos'))
    return redirect(url_for('catalogo'))


@app.route('/mp/pending')
def mp_pending():
    external_ref = request.args.get('external_reference')

    if external_ref:
        pedido = Pedido.query.get(int(external_ref))
        if pedido:
            pedido.mp_status = 'pending'
            db.session.commit()

    flash('Pago pendiente de confirmación.', 'warning')
    return _redirect_post_pago()


@app.route('/mp/webhook', methods=['POST'])
def mp_webhook():
    data  = request.get_json(silent=True) or {}
    topic = data.get('type') or request.args.get('topic')

    if topic == 'payment':
        payment_id = data.get('data', {}).get('id') or request.args.get('id')
        if payment_id:
            try:
                payment_info = sdk.payment().get(payment_id)
                payment      = payment_info.get("response", {})
                external_ref = payment.get("external_reference")
                status       = payment.get("status")

                if external_ref:
                    pedido = Pedido.query.get(int(external_ref))
                    if pedido:
                        pedido.mp_payment_id = str(payment_id)
                        pedido.mp_status     = status
                        if status == 'approved' and pedido.estado != 'Pagado':
                            pedido.estado = 'Pagado'
                            _actualizar_stock(pedido)
                        elif status == 'rejected':
                            pedido.estado = 'Rechazado'
                        db.session.commit()
            except Exception as e:
                print("Error en webhook MP:", e)

    return jsonify({"status": "ok"}), 200


# =========================
# INICIO
# =========================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()

        if not Usuario.query.filter_by(email='admin@pequemundo.cl').first():
            db.session.add(Usuario(nombre='Administrador', email='admin@pequemundo.cl',
                                   password='admin123', rol='Admin'))

        if Usuario.query.count() <= 1:
            db.session.add_all([
                Usuario(nombre='Juan Perez',  email='juan@email.com',  password='juan123',  rol='Cliente'),
                Usuario(nombre='Maria Lopez', email='maria@email.com', password='maria123', rol='Vendedor'),
            ])

        if Producto.query.count() == 0:
            db.session.add_all([
                Producto(nombre='Cuna Clásica',      descripcion='Cuna de madera sólida, segura y cómoda para bebés.',
                         imagen='peque-mueble.webp', categoria='Cunas',       precio=129990, stock=10, estado='Activo'),
                Producto(nombre='Cama Montessori',   descripcion='Cama al ras del suelo para fomentar la independencia.',
                         imagen='peque-mueble.webp', categoria='Camas',       precio=199990, stock=7,  estado='Activo'),
                Producto(nombre='Cómoda 3 Cajones',  descripcion='Cómoda infantil amplia para organizar ropa y accesorios.',
                         imagen='peque-mueble.webp', categoria='Cómodas',     precio=99990,  stock=4,  estado='Activo'),
                Producto(nombre='Escritorio Infantil', descripcion='Escritorio ergonómico ideal para estudio y manualidades.',
                         imagen='peque-mueble.webp', categoria='Escritorios', precio=89990,  stock=3,  estado='Activo'),
                Producto(nombre='Silla Infantil',    descripcion='Silla colorida y resistente para niños.',
                         imagen='peque-mueble.webp', categoria='Sillas',      precio=39990,  stock=0,  estado='Agotado'),
                Producto(nombre='Clóset Infantil',   descripcion='Clóset con diseño moderno y espacioso.',
                         imagen='peque-mueble.webp', categoria='Clósets',     precio=149990, stock=5,  estado='Activo'),
            ])

        if Pedido.query.count() == 0:
            vendedor = Usuario.query.filter_by(rol='Vendedor').first()
            db.session.add_all([
                Pedido(cliente='Juan Perez',  cliente_email='juan@email.com',  total=250000,
                       estado='Pagado',   vendedor_id=vendedor.id if vendedor else None, mp_status='approved'),
                Pedido(cliente='Maria Lopez', cliente_email='maria@email.com', total=89990,
                       estado='Pendiente', vendedor_id=vendedor.id if vendedor else None),
                Pedido(cliente='Carlos Soto', cliente_email='carlos@email.com', total=449980,
                       estado='Pagado',   vendedor_id=vendedor.id if vendedor else None, mp_status='approved'),
            ])

        db.session.commit()

    app.run(debug=True)
