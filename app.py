
import os
import re
import sqlite3
import qrcode

from io import BytesIO
from functools import wraps
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_file, abort
)

from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY", "smartdine-local-demo-secret-change-this"
)

DB_PATH = os.path.join(os.path.dirname(__file__), "smartdine.db")


# --------------------------------------------------
# DEFAULT MENU
# --------------------------------------------------

MENU = [
    ("Classic Margherita", "Pizza", 249, "Fresh mozzarella, basil and rich tomato sauce.", "🍕", "Bestseller"),
    ("Farmhouse Pizza", "Pizza", 329, "Loaded with onion, capsicum, tomato and cheese.", "🍕", "Popular"),
    ("Paneer Tikka Pizza", "Pizza", 349, "Smoky paneer tikka with spicy sauce and cheese.", "🍕", "Chef Special"),
    ("Veg Burger", "Burgers", 149, "Crispy vegetable patty with fresh lettuce.", "🍔", "Popular"),
    ("Classic Cheese Burger", "Burgers", 199, "Juicy burger with melted cheese and house sauce.", "🍔", "Bestseller"),
    ("French Fries", "Sides", 99, "Golden crispy salted fries.", "🍟", ""),
    ("Peri Peri Fries", "Sides", 129, "Crispy fries tossed in peri peri seasoning.", "🍟", "Popular"),
    ("White Sauce Pasta", "Pasta", 229, "Creamy white sauce pasta with herbs.", "🍝", "Chef Special"),
    ("Red Sauce Pasta", "Pasta", 219, "Italian-style tomato sauce pasta.", "🍝", ""),
    ("Cold Coffee", "Drinks", 119, "Chilled creamy coffee.", "🥤", "Popular"),
    ("Chocolate Shake", "Drinks", 149, "Rich chocolate milkshake.", "🥤", "Bestseller"),
    ("Brownie with Ice Cream", "Desserts", 179, "Warm chocolate brownie with vanilla ice cream.", "🍨", "Dessert"),
]


# --------------------------------------------------
# DATABASE HELPERS
# --------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_columns(conn, table_name):
    return [
        row["name"]
        for row in conn.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    ]


def add_column_if_missing(conn, table_name, column_name, definition):
    if column_name not in table_columns(conn, table_name):
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_name TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER NOT NULL,
            table_number INTEGER NOT NULL,
            active INTEGER DEFAULT 1,
            UNIQUE(restaurant_id, table_number),
            FOREIGN KEY(restaurant_id) REFERENCES restaurants(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            price REAL NOT NULL,
            description TEXT,
            emoji TEXT,
            tag TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT,
            phone TEXT,
            address TEXT,
            items TEXT,
            total REAL,
            status TEXT DEFAULT 'Received',
            created_at TEXT
        )
    """)

    # Existing database migrations
    add_column_if_missing(conn, "menu", "restaurant_id", "INTEGER")
    add_column_if_missing(conn, "menu", "is_available", "INTEGER DEFAULT 1")

    add_column_if_missing(conn, "orders", "restaurant_id", "INTEGER")
    add_column_if_missing(conn, "orders", "table_number", "INTEGER")
    add_column_if_missing(
        conn, "orders", "payment_status", "TEXT DEFAULT 'Pending'"
    )

    conn.commit()
    conn.close()


def seed_restaurant_menu(restaurant_id):
    conn = get_db()

    existing = conn.execute(
        "SELECT COUNT(*) AS total FROM menu WHERE restaurant_id = ?",
        (restaurant_id,)
    ).fetchone()["total"]

    if existing == 0:
        conn.executemany("""
            INSERT INTO menu
            (name, category, price, description, emoji, tag,
             restaurant_id, is_available)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, [(*item, restaurant_id) for item in MENU])

    conn.commit()
    conn.close()


def create_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "restaurant"


def owner_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("owner_id"):
            flash("Please login to access your restaurant dashboard.", "error")
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


# --------------------------------------------------
# CART RESPONSE HELPER
# --------------------------------------------------

def get_cart_data():
    restaurant_id = session.get("customer_restaurant_id")
    cart = session.get("cart", {})

    if not restaurant_id:
        return {
            "success": False,
            "cart": {},
            "items": [],
            "count": 0,
            "total": 0
        }

    conn = get_db()

    cart_items = []
    clean_cart = {}
    total = 0
    count = 0

    for item_id, quantity in cart.items():
        try:
            item_id = int(item_id)
            quantity = int(quantity)
        except (ValueError, TypeError):
            continue

        if quantity <= 0 or quantity > 99:
            continue

        item = conn.execute("""
            SELECT id, name, price
            FROM menu
            WHERE id = ?
            AND restaurant_id = ?
            AND is_available = 1
        """, (item_id, restaurant_id)).fetchone()

        if not item:
            continue

        subtotal = float(item["price"]) * quantity

        total += subtotal
        count += quantity
        clean_cart[str(item_id)] = quantity

        cart_items.append({
            "id": item["id"],
            "name": item["name"],
            "price": float(item["price"]),
            "quantity": quantity,
            "subtotal": round(subtotal, 2)
        })

    conn.close()

    session["cart"] = clean_cart

    return {
        "success": True,
        "cart": clean_cart,
        "items": cart_items,
        "count": count,
        "total": round(total, 2)
    }


# --------------------------------------------------
# OWNER REGISTRATION
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":

        restaurant_name = request.form.get("restaurant_name", "").strip()
        owner_name = request.form.get("owner_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not all([restaurant_name, owner_name, email, password]):
            flash("Please fill all required fields.", "error")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "error")
            return redirect(url_for("register"))

        conn = get_db()

        if conn.execute(
            "SELECT id FROM restaurants WHERE email = ?", (email,)
        ).fetchone():
            conn.close()
            flash("An account with this email already exists.", "error")
            return redirect(url_for("register"))

        base_slug = create_slug(restaurant_name)
        slug = base_slug
        suffix = 1

        while conn.execute(
            "SELECT id FROM restaurants WHERE slug = ?", (slug,)
        ).fetchone():
            suffix += 1
            slug = f"{base_slug}-{suffix}"

        cur = conn.execute("""
            INSERT INTO restaurants
            (restaurant_name, owner_name, email, password, slug, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            restaurant_name,
            owner_name,
            email,
            generate_password_hash(password),
            slug,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        restaurant_id = cur.lastrowid

        conn.executemany("""
            INSERT INTO tables (restaurant_id, table_number)
            VALUES (?, ?)
        """, [(restaurant_id, n) for n in range(1, 6)])

        conn.commit()
        conn.close()

        seed_restaurant_menu(restaurant_id)

        session.clear()
        session["owner_id"] = restaurant_id

        flash("Restaurant registered successfully!", "success")
        return redirect(url_for("admin"))

    return render_template("register.html")


# --------------------------------------------------
# OWNER LOGIN / LOGOUT
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()

        restaurant = conn.execute(
            "SELECT * FROM restaurants WHERE email = ?", (email,)
        ).fetchone()

        conn.close()

        if restaurant and check_password_hash(restaurant["password"], password):
            session.clear()
            session["owner_id"] = restaurant["id"]

            flash("Welcome back!", "success")
            return redirect(url_for("admin"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def index():
    if session.get("owner_id"):
        return redirect(url_for("admin"))

    return redirect(url_for("login"))


app.add_url_rule("/", endpoint="home", view_func=index)


# --------------------------------------------------
# CUSTOMER MENU / QR ROUTE
# --------------------------------------------------

@app.route("/r/<slug>/table/<int:table_number>")
def restaurant_table(slug, table_number):
    conn = get_db()

    restaurant = conn.execute(
        "SELECT * FROM restaurants WHERE slug = ?", (slug,)
    ).fetchone()

    if not restaurant:
        conn.close()
        abort(404)

    table = conn.execute("""
        SELECT * FROM tables
        WHERE restaurant_id = ?
        AND table_number = ?
        AND active = 1
    """, (restaurant["id"], table_number)).fetchone()

    if not table:
        conn.close()
        abort(404)

    menu = conn.execute("""
        SELECT * FROM menu
        WHERE restaurant_id = ?
        AND is_available = 1
        ORDER BY category, name
    """, (restaurant["id"],)).fetchall()

    conn.close()

    same_table = (
        session.get("customer_restaurant_id") == restaurant["id"]
        and session.get("table_number") == table_number
    )

    if not same_table:
        session["cart"] = {}

    session["customer_restaurant_id"] = restaurant["id"]
    session["customer_restaurant_slug"] = restaurant["slug"]
    session["table_number"] = table_number

    categories = sorted({
        item["category"] for item in menu if item["category"]
    })

    return render_template(
        "index.html",
        menu=menu,
        items=menu,
        categories=categories,
        restaurant=restaurant,
        table_number=table_number
    )


@app.route("/table/<int:table_number>")
def old_table_route(table_number):
    return redirect(url_for("login"))


# --------------------------------------------------
# TABLE MANAGEMENT
# --------------------------------------------------

@app.route("/admin/tables/add", methods=["POST"])
@owner_required
def add_table():
    restaurant_id = session["owner_id"]

    conn = get_db()

    next_number = conn.execute("""
        SELECT COALESCE(MAX(table_number), 0) + 1 AS next_number
        FROM tables
        WHERE restaurant_id = ?
    """, (restaurant_id,)).fetchone()["next_number"]

    conn.execute("""
        INSERT INTO tables (restaurant_id, table_number)
        VALUES (?, ?)
    """, (restaurant_id, next_number))

    conn.commit()
    conn.close()

    flash(f"Table {next_number} added successfully.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/tables/<int:table_number>/toggle", methods=["POST"])
@owner_required
def toggle_table(table_number):
    conn = get_db()

    table = conn.execute("""
        SELECT * FROM tables
        WHERE restaurant_id = ?
        AND table_number = ?
    """, (session["owner_id"], table_number)).fetchone()

    if not table:
        conn.close()
        abort(404)

    new_status = 0 if table["active"] else 1

    conn.execute("""
        UPDATE tables SET active = ?
        WHERE id = ?
        AND restaurant_id = ?
    """, (new_status, table["id"], session["owner_id"]))

    conn.commit()
    conn.close()

    flash("Table status updated.", "success")
    return redirect(url_for("admin"))


# --------------------------------------------------
# RESTAURANT MENU MANAGEMENT
# --------------------------------------------------

@app.route("/admin/menu/add", methods=["POST"])
@owner_required
def add_menu_item():

    restaurant_id = session["owner_id"]

    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    emoji = request.form.get("emoji", "🍽️").strip() or "🍽️"
    tag = request.form.get("tag", "").strip()

    try:
        price = float(request.form.get("price", "0"))
    except (ValueError, TypeError):
        flash("Please enter a valid price.", "error")
        return redirect(url_for("admin"))

    if not name or not category or price <= 0:
        flash("Dish name, category and valid price are required.", "error")
        return redirect(url_for("admin"))

    if price > 1000000:
        flash("Please enter a reasonable dish price.", "error")
        return redirect(url_for("admin"))

    conn = get_db()

    conn.execute("""
        INSERT INTO menu
        (name, category, price, description, emoji, tag,
         restaurant_id, is_available)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        name,
        category,
        price,
        description,
        emoji,
        tag,
        restaurant_id
    ))

    conn.commit()
    conn.close()

    flash(f"{name} added to your menu.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/menu/<int:item_id>/toggle", methods=["POST"])
@owner_required
def toggle_menu_item(item_id):

    restaurant_id = session["owner_id"]

    conn = get_db()

    item = conn.execute("""
        SELECT id, is_available
        FROM menu
        WHERE id = ?
        AND restaurant_id = ?
    """, (item_id, restaurant_id)).fetchone()

    if not item:
        conn.close()
        abort(404)

    new_status = 0 if item["is_available"] else 1

    conn.execute("""
        UPDATE menu
        SET is_available = ?
        WHERE id = ?
        AND restaurant_id = ?
    """, (new_status, item_id, restaurant_id))

    conn.commit()
    conn.close()

    flash("Menu item availability updated.", "success")
    return redirect(url_for("admin"))


# --------------------------------------------------
# TABLE QR CODE
# --------------------------------------------------

@app.route("/admin/qr/<int:table_number>")
@owner_required
def table_qr(table_number):

    conn = get_db()

    restaurant = conn.execute(
        "SELECT * FROM restaurants WHERE id = ?",
        (session["owner_id"],)
    ).fetchone()

    table = conn.execute("""
        SELECT * FROM tables
        WHERE restaurant_id = ?
        AND table_number = ?
        AND active = 1
    """, (session["owner_id"], table_number)).fetchone()

    conn.close()

    if not restaurant or not table:
        abort(404)

    public_url = url_for(
        "restaurant_table",
        slug=restaurant["slug"],
        table_number=table_number,
        _external=True
    )

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(public_url)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)

    return send_file(
        output,
        mimetype="image/png",
        as_attachment=True,
        download_name=f"{restaurant['slug']}-table-{table_number}-QR.png"
    )


# --------------------------------------------------
# CHECKOUT
# --------------------------------------------------

@app.route("/checkout", methods=["POST"])
def checkout():

    restaurant_id = session.get("customer_restaurant_id")
    table_number = session.get("table_number")
    slug = session.get("customer_restaurant_slug")

    if not restaurant_id or not table_number or not slug:
        flash("Please scan your restaurant table QR code first.", "error")
        return redirect(url_for("login"))

    customer = request.form.get("customer", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()

    if not customer or not phone:
        flash("Please enter your name and phone number.", "error")
        return redirect(url_for(
            "restaurant_table",
            slug=slug,
            table_number=table_number
        ))

    if not re.fullmatch(r"[0-9+\-\s()]{7,20}", phone):
        flash("Please enter a valid phone number.", "error")
        return redirect(url_for(
            "restaurant_table",
            slug=slug,
            table_number=table_number
        ))

    cart = session.get("cart", {})

    if not cart:
        flash("Your cart is empty.", "error")
        return redirect(url_for(
            "restaurant_table",
            slug=slug,
            table_number=table_number
        ))

    conn = get_db()

    active_table = conn.execute("""
        SELECT id FROM tables
        WHERE restaurant_id = ?
        AND table_number = ?
        AND active = 1
    """, (restaurant_id, table_number)).fetchone()

    if not active_table:
        conn.close()
        flash("This table is currently unavailable. Please contact staff.", "error")
        return redirect(url_for("login"))

    order_items = []
    total = 0

    for item_id, quantity in cart.items():

        try:
            item_id = int(item_id)
            quantity = int(quantity)
        except (ValueError, TypeError):
            continue

        if quantity < 1 or quantity > 99:
            continue

        item = conn.execute("""
            SELECT * FROM menu
            WHERE id = ?
            AND restaurant_id = ?
            AND is_available = 1
        """, (item_id, restaurant_id)).fetchone()

        if not item:
            continue

        subtotal = float(item["price"]) * quantity
        total += subtotal

        order_items.append(
            f"{item['name']} x {quantity} = Rs. {subtotal:.2f}"
        )

    if not order_items:
        conn.close()
        flash("No available items found in your cart.", "error")
        return redirect(url_for(
            "restaurant_table",
            slug=slug,
            table_number=table_number
        ))

    cur = conn.execute("""
        INSERT INTO orders (
            customer, phone, address, items, total, status,
            created_at, restaurant_id, table_number, payment_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        customer,
        phone,
        address,
        "\n".join(order_items),
        round(total, 2),
        "Pending Payment",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        restaurant_id,
        table_number,
        "Pending"
    ))

    order_id = cur.lastrowid

    conn.commit()
    conn.close()

    session["cart"] = {}

    return redirect(url_for("payment", order_id=order_id))


# --------------------------------------------------
# DEMO PAYMENT
# --------------------------------------------------

@app.route("/payment/<int:order_id>", methods=["GET", "POST"])
def payment(order_id):

    restaurant_id = session.get("customer_restaurant_id")

    if not restaurant_id:
        abort(403)

    conn = get_db()

    order = conn.execute("""
        SELECT * FROM orders
        WHERE id = ?
        AND restaurant_id = ?
    """, (order_id, restaurant_id)).fetchone()

    if not order:
        conn.close()
        abort(404)

    if request.method == "POST":

        if order["payment_status"] != "Paid":
            conn.execute("""
                UPDATE orders
                SET payment_status = 'Paid',
                    status = 'Received'
                WHERE id = ?
                AND restaurant_id = ?
            """, (order_id, restaurant_id))

            conn.commit()

        conn.close()

        flash("Demo payment successful! Your order has been received.", "success")
        return redirect(url_for("track", order_id=order_id))

    conn.close()

    return render_template("payment.html", order=order)


# --------------------------------------------------
# CUSTOMER ORDER TRACKING
# --------------------------------------------------

@app.route("/track/<int:order_id>")
def track(order_id):

    restaurant_id = session.get("customer_restaurant_id")

    if not restaurant_id:
        abort(403)

    conn = get_db()

    order = conn.execute("""
        SELECT * FROM orders
        WHERE id = ?
        AND restaurant_id = ?
    """, (order_id, restaurant_id)).fetchone()

    conn.close()

    if not order:
        abort(404)

    return render_template("track.html", order=order)


@app.route("/api/order-status/<int:order_id>")
def order_status_api(order_id):

    restaurant_id = session.get("customer_restaurant_id")

    if not restaurant_id:
        return jsonify({"error": "Unauthorized"}), 403

    conn = get_db()

    order = conn.execute("""
        SELECT status, payment_status
        FROM orders
        WHERE id = ?
        AND restaurant_id = ?
    """, (order_id, restaurant_id)).fetchone()

    conn.close()

    if not order:
        return jsonify({"error": "Order not found"}), 404

    return jsonify({
        "status": order["status"],
        "payment_status": order["payment_status"]
    })


# --------------------------------------------------
# OWNER ADMIN DASHBOARD
# --------------------------------------------------

@app.route("/admin")
@owner_required
def admin():

    restaurant_id = session["owner_id"]

    conn = get_db()

    restaurant = conn.execute(
        "SELECT * FROM restaurants WHERE id = ?",
        (restaurant_id,)
    ).fetchone()

    orders = conn.execute("""
        SELECT * FROM orders
        WHERE restaurant_id = ?
        ORDER BY id DESC
    """, (restaurant_id,)).fetchall()

    tables = conn.execute("""
        SELECT * FROM tables
        WHERE restaurant_id = ?
        ORDER BY table_number
    """, (restaurant_id,)).fetchall()

    stats = conn.execute("""
        SELECT
            COUNT(*) AS total_orders,

            COALESCE(SUM(
                CASE
                    WHEN payment_status = 'Paid' THEN total
                    ELSE 0
                END
            ), 0) AS revenue,

            COUNT(DISTINCT CASE
                WHEN phone IS NOT NULL AND phone != ''
                THEN phone
            END) AS customers

        FROM orders
        WHERE restaurant_id = ?
    """, (restaurant_id,)).fetchone()

    menu_count = conn.execute("""
        SELECT COUNT(*) AS total
        FROM menu
        WHERE restaurant_id = ?
    """, (restaurant_id,)).fetchone()["total"]

    menu_items = conn.execute("""
        SELECT * FROM menu
        WHERE restaurant_id = ?
        ORDER BY category, name
    """, (restaurant_id,)).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        restaurant=restaurant,
        orders=orders,
        tables=tables,
        stats=stats,
        revenue=stats["revenue"],
        count=stats["total_orders"],
        customers=stats["customers"],
        menu_count=menu_count,
        menu_items=menu_items
    )


# --------------------------------------------------
# KITCHEN DASHBOARD
# --------------------------------------------------

@app.route("/kitchen")
@owner_required
def kitchen():

    conn = get_db()

    orders = conn.execute("""
        SELECT * FROM orders
        WHERE restaurant_id = ?
        AND payment_status = 'Paid'
        AND status NOT IN ('Served', 'Cancelled', 'Delivered')
        ORDER BY id ASC
    """, (session["owner_id"],)).fetchall()

    conn.close()

    return render_template("kitchen.html", orders=orders)


# --------------------------------------------------
# UPDATE ORDER STATUS
# --------------------------------------------------

@app.route("/update-status/<int:order_id>", methods=["POST"])
@owner_required
def update_status(order_id):

    new_status = request.form.get("status", "").strip()

    allowed_statuses = [
        "Received",
        "Preparing",
        "Ready",
        "Served",
        "Out for delivery",
        "Delivered",
        "Cancelled"
    ]

    if new_status not in allowed_statuses:
        flash("Invalid order status.", "error")
        return redirect(url_for("admin"))

    conn = get_db()

    result = conn.execute("""
        UPDATE orders SET status = ?
        WHERE id = ?
        AND restaurant_id = ?
    """, (new_status, order_id, session["owner_id"]))

    conn.commit()

    updated = result.rowcount

    conn.close()

    if updated == 0:
        abort(404)

    flash("Order status updated.", "success")

    return redirect(request.referrer or url_for("admin"))


# --------------------------------------------------
# CART API
# --------------------------------------------------

@app.route("/api/cart", methods=["GET", "POST"])
def cart_api():

    if not session.get("customer_restaurant_id"):
        return jsonify({"error": "Scan a restaurant QR first"}), 403

    if request.method == "POST":

        data = request.get_json(silent=True) or {}

        try:
            item_id = int(data.get("id"))
            quantity = int(data.get("quantity"))
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid item or quantity"}), 400

        if item_id <= 0 or quantity > 99:
            return jsonify({"error": "Invalid item or quantity"}), 400

        restaurant_id = session["customer_restaurant_id"]

        conn = get_db()

        item = conn.execute("""
            SELECT id FROM menu
            WHERE id = ?
            AND restaurant_id = ?
            AND is_available = 1
        """, (item_id, restaurant_id)).fetchone()

        conn.close()

        if not item:
            return jsonify({"error": "Item not found or unavailable"}), 404

        cart = session.get("cart", {})

        if quantity <= 0:
            cart.pop(str(item_id), None)
        else:
            cart[str(item_id)] = quantity

        session["cart"] = cart

    return jsonify(get_cart_data())


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "application": "SmartDine AI"
    })


# --------------------------------------------------
# START APPLICATION
# --------------------------------------------------

if __name__ == "__main__":

    init_db()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
