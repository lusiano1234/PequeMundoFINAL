from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
import mercadopago
import os
import json

app = Flask(__name__)
app.secret_key = 'pequemundo_secret_2024'

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ─── Mercado Pago ───────────────────────────────────────────────
# Reemplaza con tu Access Token de Sandbox desde:
# https://www.mercadopago.cl/developers/panel/credentials
MP_ACCESS_TOKEN = "TEST-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

db = SQLAlchemy(app)


# =========================
# MODELOS
# =========================
class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(300), nullable=False)
    imagen = db.Column(db.String(255), nullable=False)
    categoria = db.Column(db.String(50), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(20), nullable=False)


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    telefono = db.Column(db.String(20), nullable=True)
    rol = db.Column(db.String(20), nullable=False, default='Cliente')


class Pedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(100), nullable=False)
    cliente_email = db.Column(db.String(100), nullable=True)
    total = db.Column(db.Float, nullable=False)
    estado = db.Column(db.String(30), nullable=False, default='Pendiente')
    vendedor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    mp_preference_id = db.Column(db.String(200), nullable=True)
    mp_payment_id = db.Column(db.String(200), nullable=True)
    mp_status = db.Column(db.String(50), nullable=True)
    items_json = db.Column(db.Text, nullable=True)  # JSON con productos del pedido

    vendedor = db.relationship('Usuario', backref='pedidos_vendedor', foreign_keys=[vendedor_id])


# =========================
# DECORADORES
# =========================
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
# PÁGINAS PÚBLICAS
# =========================
@app.route('/')
def inicio():
    return render_template('index.html')


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
        email = request.form['email']
        password = request.form['password']
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and usuario.password == password:
            session['usuario_id'] = usuario.id
            session['usuario'] = usuario.nombre
            session['rol'] = usuario.rol
            flash(f'¡Bienvenido, {usuario.nombre}!', 'success')

            if usuario.rol == 'Admin':
                return redirect(url_for('admin_dashboard'))
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
        nombre = request.form['nombre']
        email = request.form['email']
        telefono = request.form.get('telefono', '')
        password = request.form['password']
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
# PANEL ADMIN
# =========================
@app.route('/admin')
@requiere_admin
def admin_dashboard():
    productos = Producto.query.all()
    pedidos = Pedido.query.all()
    usuarios = Usuario.query.all()
    return render_template('dashboard.html', productos=productos, pedidos=pedidos, usuarios=usuarios)


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
            nombre=request.form['nombre'],
            descripcion=request.form['descripcion'],
            imagen=request.form['imagen'],
            categoria=request.form['categoria'],
            precio=float(request.form['precio']),
            stock=int(request.form['stock']),
            estado=request.form['estado']
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
        producto.nombre = request.form['nombre']
        producto.descripcion = request.form['descripcion']
        producto.imagen = request.form['imagen']
        producto.categoria = request.form['categoria']
        producto.precio = float(request.form['precio'])
        producto.stock = int(request.form['stock'])
        producto.estado = request.form['estado']
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
def admin_pedidos():
    pedidos = Pedido.query.all()
    return render_template('pedidos.html', pedidos=pedidos)


@app.route('/admin/usuarios')
@requiere_admin
def usuarios():
    usuarios = Usuario.query.all()
    return render_template('usuarios.html', usuarios=usuarios)


@app.route('/admin/actualizar_rol/<int:id>', methods=['POST'])
@requiere_admin
def actualizar_rol(id):
    usuario = Usuario.query.get_or_404(id)
    usuario.rol = request.form['rol']
    db.session.commit()
    flash('Rol actualizado.', 'success')
    return redirect(url_for('usuarios'))


# =========================
# PANEL VENDEDOR
# =========================
@app.route('/vendedor')
@requiere_vendedor
def vendedor_dashboard():
    vendedor_id = session.get('usuario_id')
    mis_pedidos = Pedido.query.filter_by(vendedor_id=vendedor_id).all()
    todos_pedidos = Pedido.query.all()
    productos = Producto.query.filter_by(estado='Activo').all()

    total_ventas = sum(p.total for p in mis_pedidos if p.mp_status == 'approved')
    pedidos_pendientes = sum(1 for p in mis_pedidos if p.estado == 'Pendiente')
    pedidos_pagados = sum(1 for p in mis_pedidos if p.mp_status == 'approved')

    return render_template('vendedor_dashboard.html',
                           mis_pedidos=mis_pedidos,
                           todos_pedidos=todos_pedidos,
                           productos=productos,
                           total_ventas=total_ventas,
                           pedidos_pendientes=pedidos_pendientes,
                           pedidos_pagados=pedidos_pagados)


@app.route('/vendedor/pedidos')
@requiere_vendedor
def vendedor_pedidos():
    vendedor_id = session.get('usuario_id')
    pedidos = Pedido.query.filter_by(vendedor_id=vendedor_id).all()
    return render_template('vendedor_pedidos.html', pedidos=pedidos)


@app.route('/vendedor/catalogo')
@requiere_vendedor
def vendedor_catalogo():
    productos = Producto.query.filter_by(estado='Activo').all()
    return render_template('vendedor_catalogo.html', productos=productos)


@app.route('/vendedor/crear_pedido', methods=['GET', 'POST'])
@requiere_vendedor
def vendedor_crear_pedido():
    productos = Producto.query.filter(Producto.estado == 'Activo', Producto.stock > 0).all()

    if request.method == 'POST':
        cliente = request.form['cliente']
        cliente_email = request.form['cliente_email']
        items_raw = request.form.get('items', '[]')

        try:
            items = json.loads(items_raw)
        except Exception:
            flash('Error en los productos seleccionados.', 'danger')
            return render_template('vendedor_crear_pedido.html', productos=productos)

        if not items:
            flash('Debes agregar al menos un producto.', 'danger')
            return render_template('vendedor_crear_pedido.html', productos=productos)

        total = sum(i['precio'] * i['cantidad'] for i in items)
        vendedor_id = session.get('usuario_id')

        nuevo_pedido = Pedido(
            cliente=cliente,
            cliente_email=cliente_email,
            total=total,
            estado='Pendiente',
            vendedor_id=vendedor_id,
            items_json=items_raw
        )
        db.session.add(nuevo_pedido)
        db.session.commit()

        # Crear preferencia en Mercado Pago
        mp_items = [{
            "id": str(i['id']),
            "title": i['nombre'],
            "quantity": int(i['cantidad']),
            "unit_price": float(i['precio']),
            "currency_id": "CLP"
        } for i in items]

        preference_data = {
            "items": mp_items,
            "payer": {
                "name": cliente,
                "email": cliente_email or "test_user@test.com"
            },
            "back_urls": {
                "success": url_for('mp_success', _external=True),
                "failure": url_for('mp_failure', _external=True),
                "pending": url_for('mp_pending', _external=True)
            },
            "auto_return": "approved",
            "external_reference": str(nuevo_pedido.id),
            "notification_url": url_for('mp_webhook', _external=True)
        }

        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response", {})

        if preference.get("id"):
            nuevo_pedido.mp_preference_id = preference["id"]
            db.session.commit()
            flash('Pedido creado. Redirigiendo a pago...', 'success')
            # Sandbox usa sandbox_init_point
            checkout_url = preference.get("sandbox_init_point", preference.get("init_point"))
            return redirect(checkout_url)
        else:
            flash('Pedido creado, pero hubo un error al conectar con Mercado Pago.', 'warning')
            return redirect(url_for('vendedor_pedidos'))

    return render_template('vendedor_crear_pedido.html', productos=productos)


@app.route('/vendedor/estadisticas')
@requiere_vendedor
def vendedor_estadisticas():
    vendedor_id = session.get('usuario_id')
    pedidos = Pedido.query.filter_by(vendedor_id=vendedor_id).all()

    aprobados = [p for p in pedidos if p.mp_status == 'approved']
    pendientes = [p for p in pedidos if p.estado == 'Pendiente' and p.mp_status != 'approved']
    rechazados = [p for p in pedidos if p.mp_status == 'rejected']

    total_ventas = sum(p.total for p in aprobados)
    return render_template('vendedor_estadisticas.html',
                           pedidos=pedidos,
                           aprobados=aprobados,
                           pendientes=pendientes,
                           rechazados=rechazados,
                           total_ventas=total_ventas)


# =========================
# MERCADO PAGO - CALLBACKS
# =========================
@app.route('/mp/success')
def mp_success():
    payment_id = request.args.get('payment_id')
    external_ref = request.args.get('external_reference')
    status = request.args.get('status')

    if external_ref:
        pedido = Pedido.query.get(int(external_ref))
        if pedido:
            pedido.mp_payment_id = payment_id
            pedido.mp_status = status
            if status == 'approved':
                pedido.estado = 'Pagado'
            db.session.commit()

    flash('¡Pago realizado con éxito! 🎉', 'success')
    if session.get('rol') in ('Vendedor', 'Admin'):
        return redirect(url_for('vendedor_pedidos'))
    return redirect(url_for('inicio'))


@app.route('/mp/failure')
def mp_failure():
    external_ref = request.args.get('external_reference')
    if external_ref:
        pedido = Pedido.query.get(int(external_ref))
        if pedido:
            pedido.mp_status = 'rejected'
            db.session.commit()

    flash('El pago fue rechazado. Intenta nuevamente.', 'danger')
    if session.get('rol') in ('Vendedor', 'Admin'):
        return redirect(url_for('vendedor_pedidos'))
    return redirect(url_for('inicio'))


@app.route('/mp/pending')
def mp_pending():
    external_ref = request.args.get('external_reference')
    if external_ref:
        pedido = Pedido.query.get(int(external_ref))
        if pedido:
            pedido.mp_status = 'pending'
            db.session.commit()

    flash('Pago pendiente de confirmación.', 'warning')
    if session.get('rol') in ('Vendedor', 'Admin'):
        return redirect(url_for('vendedor_pedidos'))
    return redirect(url_for('inicio'))


@app.route('/mp/webhook', methods=['POST'])
def mp_webhook():
    """Webhook para notificaciones automáticas de Mercado Pago"""
    data = request.get_json(silent=True) or {}
    topic = data.get('type') or request.args.get('topic')

    if topic == 'payment':
        payment_id = data.get('data', {}).get('id') or request.args.get('id')
        if payment_id:
            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})

            external_ref = payment.get("external_reference")
            status = payment.get("status")

            if external_ref:
                pedido = Pedido.query.get(int(external_ref))
                if pedido:
                    pedido.mp_payment_id = str(payment_id)
                    pedido.mp_status = status
                    if status == 'approved':
                        pedido.estado = 'Pagado'
                    elif status == 'rejected':
                        pedido.estado = 'Rechazado'
                    db.session.commit()

    return jsonify({"status": "ok"}), 200


# =========================
# INICIO DE LA APP
# =========================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()

        if not Usuario.query.filter_by(email='admin@pequemundo.cl').first():
            admin = Usuario(nombre='Administrador', email='admin@pequemundo.cl',
                            password='admin123', rol='Admin')
            db.session.add(admin)

        if Usuario.query.count() <= 1:
            demos = [
                Usuario(nombre='Juan Perez', email='juan@email.com', password='juan123', rol='Cliente'),
                Usuario(nombre='Maria Lopez', email='maria@email.com', password='maria123', rol='Vendedor'),
            ]
            db.session.add_all(demos)

        if Producto.query.count() == 0:
            productos_demo = [
                Producto(nombre='Cuna Clásica', descripcion='Cuna de madera sólida, segura y cómoda para bebés.',
                         imagen='peque-mueble.webp', categoria='Cunas', precio=129990, stock=10, estado='Activo'),
                Producto(nombre='Cama Montessori', descripcion='Cama al ras del suelo para fomentar la independencia.',
                         imagen='peque-mueble.webp', categoria='Camas', precio=199990, stock=7, estado='Activo'),
                Producto(nombre='Cómoda 3 Cajones', descripcion='Cómoda infantil amplia para organizar ropa y accesorios.',
                         imagen='peque-mueble.webp', categoria='Cómodas', precio=99990, stock=4, estado='Activo'),
                Producto(nombre='Escritorio Infantil', descripcion='Escritorio ergonómico ideal para estudio y manualidades.',
                         imagen='peque-mueble.webp', categoria='Escritorios', precio=89990, stock=3, estado='Activo'),
                Producto(nombre='Silla Infantil', descripcion='Silla colorida y resistente para niños.',
                         imagen='peque-mueble.webp', categoria='Sillas', precio=39990, stock=0, estado='Agotado'),
                Producto(nombre='Clóset Infantil', descripcion='Clóset con diseño moderno y espacioso.',
                         imagen='peque-mueble.webp', categoria='Clósets', precio=149990, stock=5, estado='Activo'),
            ]
            db.session.add_all(productos_demo)

        if Pedido.query.count() == 0:
            vendedor = Usuario.query.filter_by(rol='Vendedor').first()
            pedidos_demo = [
                Pedido(cliente='Juan Perez', cliente_email='juan@email.com', total=250000,
                       estado='Pagado', vendedor_id=vendedor.id if vendedor else None, mp_status='approved'),
                Pedido(cliente='Maria Lopez', cliente_email='maria@email.com', total=89990,
                       estado='Pendiente', vendedor_id=vendedor.id if vendedor else None),
                Pedido(cliente='Carlos Soto', cliente_email='carlos@email.com', total=449980,
                       estado='Pagado', vendedor_id=vendedor.id if vendedor else None, mp_status='approved'),
            ]
            db.session.add_all(pedidos_demo)

        db.session.commit()

    app.run(debug=True)
