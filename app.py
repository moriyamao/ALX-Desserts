import os
import csv
import json
import uuid
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash, jsonify
from groq import Groq
from functools import wraps
from dotenv import load_dotenv

load_dotenv('sandbox.env')

app = Flask(__name__)
app.secret_key = "alx_desserts_secret_key"
app.config['UPLOAD_FOLDER'] = 'static/images'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ── Low Stock Threshold ──────────────────────────────────────────────────────
LOW_STOCK_THRESHOLD = 5

# ── VAT / Senior-PWD Discount Settings ───────────────────────────────────────
VAT_RATE = 0.12                  # 12% Philippine VAT
SENIOR_PWD_DISCOUNT_RATE = 0.20  # 20% statutory discount for Senior Citizens / PWD

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE = 'dessert_shop.db'


def get_db():
    """Open a database connection with dict-like row access."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist yet."""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                item     TEXT PRIMARY KEY,
                quantity INTEGER NOT NULL DEFAULT 0,
                cost     REAL    NOT NULL DEFAULT 0,
                price    REAL    NOT NULL DEFAULT 0,
                image    TEXT    NOT NULL DEFAULT 'default.png'
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id       TEXT    NOT NULL,
                customer       TEXT    NOT NULL,
                item           TEXT    NOT NULL,
                quantity       INTEGER NOT NULL,
                price          REAL    NOT NULL,
                profit         REAL    NOT NULL,
                total          REAL    NOT NULL,
                payment_method TEXT    NOT NULL DEFAULT 'cash',
                datetime       TEXT    NOT NULL,
                amount_received REAL   DEFAULT 0,
                change_amount   REAL   DEFAULT 0
            )
        ''')
        try:
            conn.execute('ALTER TABLE orders ADD COLUMN amount_received REAL DEFAULT 0')
            conn.execute('ALTER TABLE orders ADD COLUMN change_amount REAL DEFAULT 0')
        except sqlite3.OperationalError:
            pass

        # ── VAT / Senior-PWD discount columns ───────────────────────────────
        for col_def in [
            'gross_amount REAL DEFAULT 0',        # price*qty before any discount
            "discount_type TEXT DEFAULT 'none'",  # none | senior | pwd
            "discount_name TEXT DEFAULT ''",      # name printed on the SC/PWD ID
            "discount_id TEXT DEFAULT ''",        # SC/PWD ID number
            'discount_amount REAL DEFAULT 0',     # peso amount discounted
            'vat_amount REAL DEFAULT 0',          # VAT portion of the total (0 if exempt)
        ]:
            try:
                conn.execute(f'ALTER TABLE orders ADD COLUMN {col_def}')
            except sqlite3.OperationalError:
                pass

        conn.execute('''
            CREATE TABLE IF NOT EXISTS archived_orders (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id    INTEGER,
                order_id       TEXT    NOT NULL,
                customer       TEXT    NOT NULL,
                item           TEXT    NOT NULL,
                quantity       INTEGER NOT NULL,
                price          REAL    NOT NULL,
                profit         REAL    NOT NULL,
                total          REAL    NOT NULL,
                payment_method TEXT    NOT NULL DEFAULT 'cash',
                datetime       TEXT    NOT NULL,
                reason         TEXT    NOT NULL DEFAULT 'voided',
                archived_at    TEXT    NOT NULL,
                amount_received REAL   DEFAULT 0,
                change_amount   REAL   DEFAULT 0
            )
        ''')
        try:
            conn.execute('ALTER TABLE archived_orders ADD COLUMN amount_received REAL DEFAULT 0')
            conn.execute('ALTER TABLE archived_orders ADD COLUMN change_amount REAL DEFAULT 0')
        except sqlite3.OperationalError:
            pass

        for col_def in [
            'gross_amount REAL DEFAULT 0',
            "discount_type TEXT DEFAULT 'none'",
            "discount_name TEXT DEFAULT ''",
            "discount_id TEXT DEFAULT ''",
            'discount_amount REAL DEFAULT 0',
            'vat_amount REAL DEFAULT 0',
        ]:
            try:
                conn.execute(f'ALTER TABLE archived_orders ADD COLUMN {col_def}')
            except sqlite3.OperationalError:
                pass

        conn.execute('''
            CREATE TABLE IF NOT EXISTS app_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        conn.commit()


def migrate_from_csv():
    """
    One-time migration: copy existing CSV data into SQLite the first time
    the app runs. Skips migration if the tables already have data.
    """
    with get_db() as conn:
        # ── Inventory ────────────────────────────────────────────────────────
        if conn.execute('SELECT COUNT(*) FROM inventory').fetchone()[0] == 0:
            try:
                with open('inventory.csv', newline='', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        conn.execute(
                            'INSERT OR IGNORE INTO inventory (item, quantity, cost, price, image) VALUES (?,?,?,?,?)',
                            (row['Item'], int(row['Quantity']),
                             float(row['Cost']), float(row['Price']), row['Image'])
                        )
                print("[DB] Inventory migrated from inventory.csv")
            except FileNotFoundError:
                pass

        # ── Orders ───────────────────────────────────────────────────────────
        if conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0:
            try:
                with open('orders.csv', newline='', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        # Skip summary rows written by the old save_orders()
                        first = row.get('OrderID', row.get('Customer', ''))
                        if first in ('TOTAL', 'PAYMENT SUMMARY',
                                     'Cash Sales', 'GCash Sales', 'PayPal Sales', ''):
                            continue
                        if row.get('Customer', '') in (
                                'TOTAL', 'PAYMENT SUMMARY', 'Cash Sales', 'GCash Sales', ''):
                            continue
                        try:
                            quantity = int(row['Quantity'])
                            price = float(row['Price'])
                            profit = float(row['Profit'])
                            total = float(row['Total'])
                        except (ValueError, KeyError):
                            continue
                        conn.execute(
                            '''INSERT INTO orders
                               (order_id, customer, item, quantity, price, profit, total, payment_method, datetime,
                                gross_amount, discount_type, discount_name, discount_id, discount_amount, vat_amount)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (
                                row.get('OrderID', str(uuid.uuid4())[:8].upper()),
                                row['Customer'],
                                row['Item'],
                                quantity, price, profit, total,
                                row.get('PaymentMethod', 'cash'),
                                row.get('DateTime', ''),
                                total, 'none', '', '', 0.0, 0.0
                            )
                        )
                print("[DB] Orders migrated from orders.csv")
            except FileNotFoundError:
                pass

        conn.commit()


def migrate_prices_to_vat_inclusive():
    """
    One-time migration: the prices originally entered in Inventory were
    pre-tax (VAT not yet applied). This bakes 12% VAT into every item's
    price and rounds to the nearest whole peso, e.g. ₱120.00 -> ₱134.00
    (120 * 1.12 = 134.40, rounded down to 134).

    Runs exactly once — guarded by an app_meta flag — so prices already
    migrated are never re-taxed on a later restart, and any new items
    added afterward (already VAT-inclusive when typed in) are untouched.
    """
    with get_db() as conn:
        already_done = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'vat_price_migration_done'"
        ).fetchone()
        if already_done:
            return

        rows = conn.execute('SELECT item, price FROM inventory').fetchall()
        for row in rows:
            new_price = float(int(row['price'] * (1 + VAT_RATE) + 0.5))  # round to nearest whole peso
            conn.execute('UPDATE inventory SET price = ? WHERE item = ?', (new_price, row['item']))

        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES ('vat_price_migration_done', '1')"
        )
        conn.commit()
        if rows:
            print(f"[DB] VAT migration: {len(rows)} inventory price(s) updated to VAT-inclusive whole-peso amounts.")


# ── Initialise on startup ────────────────────────────────────────────────────
init_db()
migrate_from_csv()
migrate_prices_to_vat_inclusive()


# ── DB Query Helpers ─────────────────────────────────────────────────────────
def db_get_inventory():
    """Return inventory as a dict: {item_name: {quantity, cost, price, image}}"""
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM inventory').fetchall()
    return {row['item']: {
        'quantity': row['quantity'],
        'cost': row['cost'],
        'price': row['price'],
        'image': row['image'],
    } for row in rows}


def db_get_orders():
    """Return all orders as a list of dicts, oldest first."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM orders ORDER BY id ASC'
        ).fetchall()
    return [dict(row) for row in rows]


def db_get_sales():
    """
    Compute the per-item sales summary from the orders table.
    Returns: {item: {total_sold, profit, customers}}
    Items in inventory that have no orders are included with zero values.
    """
    with get_db() as conn:
        # Aggregate sales per item
        agg_rows = conn.execute('''
            SELECT item,
                   SUM(quantity) AS total_sold,
                   SUM(profit)   AS profit
            FROM orders
            GROUP BY item
        ''').fetchall()

        # Build a dict first
        sales = {}
        for row in agg_rows:
            cust_rows = conn.execute(
                'SELECT DISTINCT customer FROM orders WHERE item = ?',
                (row['item'],)
            ).fetchall()
            sales[row['item']] = {
                'total_sold': row['total_sold'] or 0,
                'profit': row['profit'] or 0.0,
                'customers': [c['customer'] for c in cust_rows],
            }

        # Ensure every inventory item has an entry (even if unsold)
        inv_items = conn.execute('SELECT item FROM inventory').fetchall()
        for inv_row in inv_items:
            if inv_row['item'] not in sales:
                sales[inv_row['item']] = {'total_sold': 0, 'profit': 0.0, 'customers': []}

    return sales


def db_get_archived_orders():
    """Return all archived orders as a list of dicts, newest first."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM archived_orders ORDER BY id DESC'
        ).fetchall()
    return [dict(row) for row in rows]


def db_get_trend_summary(timeframe="monthly"):
    """
    Returns aggregated sales data by day, month, or year using SQLite GROUP BY.
    """
    if timeframe == 'daily':
        substr_len = 10  # YYYY-MM-DD
    elif timeframe == 'yearly':
        substr_len = 4  # YYYY
    else:  # monthly
        substr_len = 7  # YYYY-MM

    with get_db() as conn:
        rows = conn.execute(f'''
            SELECT SUBSTR(datetime, 1, {substr_len}) AS key,
                   SUM(total) AS sales,
                   SUM(profit) AS profit,
                   COUNT(*) AS count
            FROM orders
            WHERE datetime != ''
            GROUP BY key
            ORDER BY key DESC
        ''').fetchall()

    summary = []
    for row in rows:
        key = row['key']
        if timeframe == 'daily':
            label = datetime.strptime(key, "%Y-%m-%d").strftime("%b %d, %Y")
        elif timeframe == 'monthly':
            label = datetime.strptime(key, "%Y-%m").strftime("%B %Y")
        else:
            label = key

        summary.append({
            'key': key,
            'label': label,
            'sales': row['sales'] or 0.0,
            'profit': row['profit'] or 0.0,
            'count': row['count'] or 0
        })
    return summary


# ─── Auth Decorator ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# ─── Auth Routes ─────────────────────────────────────────────────────────────
@app.route("/login", methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] != 'admin' or request.form['password'] != 'admin123':
            error = 'Invalid Credentials. Please try again.'
        else:
            session['logged_in'] = True
            flash('You were successfully logged in', 'success')
            return redirect(url_for('index'))
    return render_template('login.html', error=error)


@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    flash('You were successfully logged out', 'success')
    return redirect(url_for('login'))


# ─── Page Routes ─────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    inventory = db_get_inventory()
    orders = db_get_orders()
    return render_template("index.html",
                           inventory=inventory,
                           orders=orders,
                           low_stock_threshold=LOW_STOCK_THRESHOLD)


@app.route("/inventory")
@login_required
def inventory_page():
    inventory = db_get_inventory()
    sales = db_get_sales()
    low_stock_items = [item for item, data in inventory.items()
                       if data['quantity'] <= LOW_STOCK_THRESHOLD]
    return render_template("inventory.html",
                           inventory=inventory,
                           sales=sales,
                           low_stock_items=low_stock_items,
                           low_stock_threshold=LOW_STOCK_THRESHOLD)


@app.route("/sales")
@login_required
def sales_page():
    orders = db_get_orders()
    sales = db_get_sales()

    total_profit = sum(o["profit"] for o in orders)
    gross_sales = sum(o["total"] for o in orders)
    total_orders = len(orders)
    total_vat_collected = sum(o.get("vat_amount") or 0 for o in orders)
    total_senior_pwd_discount = sum(o.get("discount_amount") or 0 for o in orders)
    senior_pwd_order_count = len(set(
        o["order_id"] for o in orders if (o.get("discount_type") or "none") in ("senior", "pwd")
    ))

    # ── Database Sales Calculation ────────────────────────────────────────────
    daily_summary = db_get_trend_summary('daily')
    monthly_summary = db_get_trend_summary('monthly')
    yearly_summary = db_get_trend_summary('yearly')

    current_month_key = datetime.now().strftime("%Y-%m")
    current_month_label = datetime.now().strftime("%B %Y")

    current_month_sales = 0.0
    current_month_profit = 0.0
    current_month_orders = 0
    for m in monthly_summary:
        if m['key'] == current_month_key:
            current_month_sales = m['sales']
            current_month_profit = m['profit']
            current_month_orders = m['count']
            break

    return render_template("sales.html",
                           sales=sales,
                           orders=orders,
                           total_profit=total_profit,
                           gross_sales=gross_sales,
                           total_orders=total_orders,
                           total_vat_collected=total_vat_collected,
                           total_senior_pwd_discount=total_senior_pwd_discount,
                           senior_pwd_order_count=senior_pwd_order_count,
                           daily_summary=daily_summary,
                           monthly_summary=monthly_summary,
                           yearly_summary=yearly_summary,
                           current_month_label=current_month_label,
                           current_month_sales=current_month_sales,
                           current_month_profit=current_month_profit,
                           current_month_orders=current_month_orders)


# ─── Receipt Page ─────────────────────────────────────────────────────────────
@app.route("/receipt/<order_id>")
@login_required
def receipt(order_id):
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM orders WHERE order_id = ? ORDER BY id ASC',
            (order_id,)
        ).fetchall()

    receipt_orders = [dict(r) for r in rows]

    if not receipt_orders:
        flash("Receipt not found.", "danger")
        return redirect(url_for('index'))

    customer = receipt_orders[0]['customer']
    payment_method = receipt_orders[0].get('payment_method', 'cash')
    dt_str = receipt_orders[0].get('datetime', '')
    amount_received = receipt_orders[0].get('amount_received', 0)
    change_amount = receipt_orders[0].get('change_amount', 0)
    discount_type = receipt_orders[0].get('discount_type', 'none') or 'none'
    discount_name = receipt_orders[0].get('discount_name', '')
    discount_id = receipt_orders[0].get('discount_id', '')

    receipt_total = sum(o['total'] for o in receipt_orders)
    receipt_gross = sum(o.get('gross_amount') or o['total'] for o in receipt_orders)
    receipt_discount = sum(o.get('discount_amount') or 0 for o in receipt_orders)
    receipt_vat = sum(o.get('vat_amount') or 0 for o in receipt_orders)

    return render_template("receipt.html",
                           order_id=order_id,
                           customer=customer,
                           payment_method=payment_method,
                           dt_str=dt_str,
                           receipt_orders=receipt_orders,
                           receipt_total=receipt_total,
                           receipt_gross=receipt_gross,
                           receipt_discount=receipt_discount,
                           receipt_vat=receipt_vat,
                           amount_received=amount_received,
                           change_amount=change_amount,
                           discount_type=discount_type,
                           discount_name=discount_name,
                           discount_id=discount_id)


# ─── Inventory Operations ────────────────────────────────────────────────────
@app.route("/update_inventory", methods=["POST"])
@login_required
def update_inventory():
    item_name = request.form["item_name"]
    quantity = int(request.form["quantity"])
    cost = float(request.form["cost"])
    price = float(request.form["price"])
    image_file = request.files.get("image")

    # Handle image upload
    with get_db() as conn:
        existing = conn.execute(
            'SELECT image FROM inventory WHERE item = ?', (item_name,)
        ).fetchone()

    if image_file and image_file.filename != '':
        filename = image_file.filename
        image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    else:
        filename = existing['image'] if existing else 'default.png'

    with get_db() as conn:
        conn.execute('''
            INSERT INTO inventory (item, quantity, cost, price, image)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item) DO UPDATE SET
                quantity = excluded.quantity,
                cost     = excluded.cost,
                price    = excluded.price,
                image    = excluded.image
        ''', (item_name, quantity, cost, price, filename))
        conn.commit()

    flash(f"Item '{item_name}' successfully updated.", "success")
    return redirect(url_for("inventory_page"))


@app.route("/delete_inventory/<item>")
@login_required
def delete_inventory(item):
    with get_db() as conn:
        # Remove the item and all its associated order records
        conn.execute('DELETE FROM inventory WHERE item = ?', (item,))
        conn.execute('DELETE FROM orders WHERE item = ?', (item,))
        conn.commit()
    flash(f"Item '{item}' removed from inventory.", "success")
    return redirect(url_for("inventory_page"))


# ─── Cart Order (multiple items) ─────────────────────────────────────────────
@app.route("/add_cart_order", methods=["POST"])
@login_required
def add_cart_order():
    customer = request.form["customer"]
    payment_method = request.form.get("payment_method", "cash")
    cart = json.loads(request.form.get("cart", "[]"))

    # ── Senior Citizen / PWD discount info ──────────────────────────────────
    discount_type = request.form.get("discount_type", "none").strip().lower()
    discount_name = request.form.get("discount_name", "").strip()
    discount_id = request.form.get("discount_id", "").strip()

    if discount_type not in ("senior", "pwd"):
        discount_type = "none"

    # Require name + ID before honoring the discount; otherwise fall back to none
    if discount_type in ("senior", "pwd") and (not discount_name or not discount_id):
        flash("Senior/PWD discount requires both a name and an ID number — order placed without the discount.",
              "danger")
        discount_type = "none"
        discount_name = ""
        discount_id = ""

    amount_received = 0.0
    change_amount = 0.0
    if payment_method == 'cash':
        try:
            amount_received = float(request.form.get("amount_received", 0))
            change_amount = float(request.form.get("change_amount", 0))
        except ValueError:
            pass

    if not cart:
        flash("Cart is empty.", "danger")
        return redirect(url_for("index"))

    shared_order_id = str(uuid.uuid4())[:8].upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    succeeded = []
    failed = []
    last_order_id = None

    for cart_item in cart:
        item = cart_item["item"]
        quantity = int(cart_item["quantity"])
        if _process_order(customer, item, quantity,
                          payment_method=payment_method,
                          amount_received=amount_received,
                          change_amount=change_amount,
                          order_id=shared_order_id,
                          dt_str=now_str,
                          discount_type=discount_type,
                          discount_name=discount_name,
                          discount_id=discount_id):
            succeeded.append(f"{quantity}x {item}")
            last_order_id = shared_order_id
        else:
            failed.append(item)

    if succeeded:
        flash(f"Order placed: {', '.join(succeeded)}.", "success")
    for f_item in failed:
        flash(f"Not enough stock for '{f_item}'.", "danger")

    if last_order_id:
        return redirect(url_for('receipt', order_id=last_order_id))

    return redirect(url_for("index"))


# ─── Single Order (backwards compat) ─────────────────────────────────────────
@app.route("/add_order", methods=["POST"])
@login_required
def add_order():
    customer = request.form["customer"]
    item = request.form["item"]
    quantity = int(request.form["quantity"])
    payment_method = request.form.get("payment_method", "cash")
    order_id = str(uuid.uuid4())[:8].upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if _process_order(customer, item, quantity,
                      payment_method=payment_method,
                      order_id=order_id,
                      dt_str=now_str):
        flash(f"Order for {quantity}x {item} added successfully!", "success")
        return redirect(url_for('receipt', order_id=order_id))
    else:
        flash(f"Failed to add order: Not enough stock for {item}.", "danger")
        return redirect(url_for("index"))


def _calculate_order_pricing(gross, discount_type):
    """
    Pure pricing calculation shared by the order processor.

    Regular order:
        Prices are VAT-inclusive already. We just split the existing total
        into its VAT and VAT-exclusive base for display/reporting.
        discount_amount = 0, vat_amount = gross * 12/112, total = gross

    Senior / PWD order:
        20% statutory discount applied to the (VAT-inclusive) price, and the
        result is VAT-exempt -> no VAT is charged on top.
        discount_amount = gross * 20%, total = gross - discount_amount, vat_amount = 0
    """
    gross = round(gross, 2)
    if discount_type in ("senior", "pwd"):
        discount_amount = round(gross * SENIOR_PWD_DISCOUNT_RATE, 2)
        total = round(gross - discount_amount, 2)
        vat_amount = 0.0
    else:
        discount_amount = 0.0
        total = gross
        vat_amount = round(total * VAT_RATE / (1 + VAT_RATE), 2)
    return total, discount_amount, vat_amount


def _process_order(customer, item, quantity, payment_method="cash", amount_received=0.0, change_amount=0.0,
                   order_id=None, dt_str=None, discount_type="none", discount_name="", discount_id=""):
    """
    Core order processing against SQLite.
    Deducts inventory and inserts an order row atomically.
    Returns True on success, False if stock is insufficient.
    """
    with get_db() as conn:
        inv_row = conn.execute(
            'SELECT * FROM inventory WHERE item = ?', (item,)
        ).fetchone()

        if inv_row is None or inv_row['quantity'] < quantity:
            return False

        selling_price = inv_row['price']
        cost_price = inv_row['cost']
        gross_amount = round(selling_price * quantity, 2)

        total, discount_amount, vat_amount = _calculate_order_pricing(gross_amount, discount_type)
        total_profit = round(total - (cost_price * quantity), 2)

        oid = order_id or str(uuid.uuid4())[:8].upper()
        dt = dt_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Deduct stock
        conn.execute(
            'UPDATE inventory SET quantity = quantity - ? WHERE item = ?',
            (quantity, item)
        )

        # Record order
        conn.execute('''
            INSERT INTO orders
                (order_id, customer, item, quantity, price, profit, total, payment_method, datetime,
                 amount_received, change_amount, gross_amount, discount_type, discount_name, discount_id,
                 discount_amount, vat_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (oid, customer, item, quantity, selling_price, total_profit, total, payment_method, dt,
              amount_received, change_amount, gross_amount, discount_type, discount_name, discount_id,
              discount_amount, vat_amount))

        conn.commit()

    return True


# ─── Sales Operations ─────────────────────────────────────────────────────────
@app.route('/delete_all_sales')
@login_required
def delete_all_sales():
    """Archives every active order and restores inventory, instead of permanently deleting."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        order_rows = conn.execute('SELECT * FROM orders').fetchall()
        for row in order_rows:
            # Move to archive
            conn.execute('''
                INSERT INTO archived_orders
                    (original_id, order_id, customer, item, quantity, price,
                     profit, total, payment_method, datetime, reason, archived_at, amount_received, change_amount,
                     gross_amount, discount_type, discount_name, discount_id, discount_amount, vat_amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (row['id'], row['order_id'], row['customer'], row['item'],
                  row['quantity'], row['price'], row['profit'], row['total'],
                  row['payment_method'], row['datetime'], 'cleared', now_str,
                  row['amount_received'], row['change_amount'],
                  row['gross_amount'], row['discount_type'], row['discount_name'], row['discount_id'],
                  row['discount_amount'], row['vat_amount']))
            # Restore inventory
            conn.execute(
                'UPDATE inventory SET quantity = quantity + ? WHERE item = ?',
                (row['quantity'], row['item'])
            )
        conn.execute('DELETE FROM orders')
        conn.commit()

    flash('All sales records archived and inventory restored.', 'success')
    return redirect(url_for('sales_page'))


@app.route("/delete_sale/<int:row_id>")
@login_required
def delete_sale(row_id):
    """Archives a single order and restores its inventory quantity."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        order = conn.execute(
            'SELECT * FROM orders WHERE id = ?', (row_id,)
        ).fetchone()

        if order:
            # Move to archive
            conn.execute('''
                INSERT INTO archived_orders
                    (original_id, order_id, customer, item, quantity, price,
                     profit, total, payment_method, datetime, reason, archived_at, amount_received, change_amount,
                     gross_amount, discount_type, discount_name, discount_id, discount_amount, vat_amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (order['id'], order['order_id'], order['customer'], order['item'],
                  order['quantity'], order['price'], order['profit'], order['total'],
                  order['payment_method'], order['datetime'], 'voided', now_str,
                  order['amount_received'], order['change_amount'],
                  order['gross_amount'], order['discount_type'], order['discount_name'], order['discount_id'],
                  order['discount_amount'], order['vat_amount']))
            # Restore inventory
            conn.execute(
                'UPDATE inventory SET quantity = quantity + ? WHERE item = ?',
                (order['quantity'], order['item'])
            )
            conn.execute('DELETE FROM orders WHERE id = ?', (row_id,))
            conn.commit()
            flash("Sale voided and moved to archive. Inventory restored.", "success")

    return redirect(url_for("sales_page"))


# ─── Archive Page ─────────────────────────────────────────────────────────────
@app.route("/archive")
@login_required
def archive_page():
    """Displays all archived (voided/cleared) sales records."""
    archived = db_get_archived_orders()
    total_archived = len(archived)
    total_archived_value = sum(o['total'] for o in archived)
    total_archived_profit = sum(o['profit'] for o in archived)
    return render_template("archive.html",
                           archived=archived,
                           total_archived=total_archived,
                           total_archived_value=total_archived_value,
                           total_archived_profit=total_archived_profit)


@app.route("/download_archive_csv")
@login_required
def download_archive_csv():
    """Generates and downloads a CSV of all archived records."""
    archived = db_get_archived_orders()

    with open('archived_orders.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['ArchiveID', 'OriginalOrderID', 'Customer', 'Item',
                         'Quantity', 'Price', 'Profit', 'Total',
                         'PaymentMethod', 'OriginalDateTime', 'Reason', 'ArchivedAt',
                         'GrossAmount', 'DiscountType', 'DiscountName', 'DiscountID',
                         'DiscountAmount', 'VATAmount'])
        for o in archived:
            writer.writerow([
                o['id'],
                o['order_id'],
                o['customer'],
                o['item'],
                o['quantity'],
                o['price'],
                o['profit'],
                o['total'],
                o['payment_method'],
                o['datetime'],
                o['reason'],
                o['archived_at'],
                o.get('gross_amount', 0),
                o.get('discount_type', 'none'),
                o.get('discount_name', ''),
                o.get('discount_id', ''),
                o.get('discount_amount', 0),
                o.get('vat_amount', 0),
            ])

        # Summary
        total_value = sum(o['total'] for o in archived)
        total_profit = sum(o['profit'] for o in archived)
        writer.writerow([])
        writer.writerow(['TOTAL ARCHIVED', '', '', '', len(archived),
                         '', round(total_profit, 2), round(total_value, 2), '', '', '', ''])

    return send_file('archived_orders.csv', as_attachment=True)


@app.route("/download_sales_csv")
@login_required
def download_sales_csv():
    """
    Generates a fresh orders.csv from the database and sends it for download.
    """
    orders = db_get_orders()

    with open('orders.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['OrderID', 'Customer', 'Item', 'Quantity',
                         'Price', 'Profit', 'Total', 'PaymentMethod', 'DateTime',
                         'GrossAmount', 'DiscountType', 'DiscountName', 'DiscountID',
                         'DiscountAmount', 'VATAmount'])
        for order in orders:
            writer.writerow([
                order.get('order_id', ''),
                order['customer'],
                order['item'],
                order['quantity'],
                order['price'],
                order['profit'],
                order['total'],
                order.get('payment_method', 'cash'),
                order.get('datetime', ''),
                order.get('gross_amount', 0),
                order.get('discount_type', 'none'),
                order.get('discount_name', ''),
                order.get('discount_id', ''),
                order.get('discount_amount', 0),
                order.get('vat_amount', 0),
            ])

        # Summary rows
        total_profit = sum(o['profit'] for o in orders)
        gross_sales = sum(o['total'] for o in orders)
        cash_total = sum(o['total'] for o in orders if o.get('payment_method') == 'cash')
        gcash_total = sum(o['total'] for o in orders if o.get('payment_method') == 'gcash')
        total_items = sum(o['quantity'] for o in orders)
        total_vat = sum(o.get('vat_amount', 0) for o in orders)
        total_discount = sum(o.get('discount_amount', 0) for o in orders)

        writer.writerow([])
        writer.writerow(['TOTAL', '', '', total_items, '', round(total_profit, 2), round(gross_sales, 2), '', ''])
        writer.writerow(['Total VAT Collected', '', '', '', '', '', round(total_vat, 2), '', ''])
        writer.writerow(['Total Senior/PWD Discounts', '', '', '', '', '', round(total_discount, 2), '', ''])
        writer.writerow([])
        writer.writerow(['PAYMENT SUMMARY', '', '', '', '', '', '', '', ''])
        writer.writerow(['Cash Sales', '', '', '', '', '', round(cash_total, 2), 'cash', ''])
        writer.writerow(['GCash Sales', '', '', '', '', '', round(gcash_total, 2), 'gcash', ''])

    return send_file('orders.csv', as_attachment=True)


@app.route("/download_trend_csv/<timeframe>")
@login_required
def download_trend_csv(timeframe):
    """
    Generates a CSV of the trend data (daily, monthly, or yearly) from the database.
    """
    if timeframe not in ['daily', 'monthly', 'yearly']:
        timeframe = 'monthly'

    summary = db_get_trend_summary(timeframe)

    filename = f"sales_trend_{timeframe}.csv"
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Period', 'Gross Sales', 'Net Profit', 'Total Orders'])
        for row in summary:
            writer.writerow([row['label'], round(row['sales'], 2), round(row['profit'], 2), row['count']])

    return send_file(filename, as_attachment=True)


# ── Groq AI Chatbot ──────────────────────────────────────────────────────────
# The system prompt is rebuilt from the live database on every request so the
# chatbot always reflects the current menu, prices, and stock — never a stale
# hardcoded snapshot. It is scoped to front-end / how-to-use-the-app questions
# only; it does not discuss backend implementation, code, or architecture.

CHAT_HISTORY_LIMIT = 10  # max prior turns sent to the model
CHAT_MAX_TOKENS = 512
CHAT_MODEL = "llama-3.3-70b-versatile"


def build_alx_system_prompt():
    """
    Builds the chatbot's system prompt fresh from live data every call.
    Keeping this dynamic means any inventory/price/stock change is reflected
    immediately — no manual prompt editing needed when the menu changes.
    """
    inventory = db_get_inventory()
    low_stock_items = [item for item, d in inventory.items()
                       if d['quantity'] <= LOW_STOCK_THRESHOLD]

    if inventory:
        menu_lines = "\n".join(
            f"- {item}: ₱{data['price']:.2f}"
            f"{' (LOW STOCK)' if item in low_stock_items else ''}"
            for item, data in inventory.items()
        )
    else:
        menu_lines = "(No products in inventory yet.)"

    return f"""
You are ALX Assistant, the in-app helper for ALX Desserts' point-of-sale
website. You help the shop admin use the website itself — never the code
behind it.

CURRENT MENU (live, updates automatically):
{menu_lines}

PRICING RULES (for explaining to the admin, not for calculating live sales):
- Listed prices are VAT-inclusive. VAT is 12%.
- Senior Citizens and PWD customers get a 20% discount and are VAT-exempt,
  per Philippine law. The cashier selects Senior or PWD on the POS screen
  and enters the customer's name and ID number before completing the order.

WHAT YOU CAN HELP WITH:
1. Menu items, current prices, and stock availability listed above
2. How to use the website's pages and buttons: Dashboard/POS, Inventory,
   Sales, Archive, and the Login screen
3. Placing an order on the POS screen, choosing Cash or GCash payment, and
   applying the Senior/PWD discount (selecting the type and entering the
   required name + ID)
4. Adding, editing, or deleting an inventory item from the Inventory page
5. Reading sales reports, trend charts, and downloading CSV exports
6. Voiding a sale and finding it again in the Archive
7. General tips for running the shop day-to-day using this website

WHAT YOU MUST NOT DO:
- Do not explain or discuss the app's source code, file structure, database
  schema, programming language, frameworks, API keys, or how any feature is
  implemented behind the scenes. If asked about implementation details,
  say that's outside what you can help with and redirect to how to use the
  feature instead.
- Do not answer questions unrelated to ALX Desserts or this website
  (general cooking, unrelated software, personal advice, etc.) — politely
  decline and redirect back to the website/shop.

Keep answers concise, friendly, and practical. Match the language of the
conversation: if the user has been writing in English, reply in English;
if in Filipino/Tagalog, reply in Filipino/Tagalog. Stay consistent within
a conversation rather than switching languages mid-reply, and don't
narrate or apologize about which language you're using — just respond
naturally in it.
""".strip()


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    """Groq AI chatbot endpoint — front-end/how-to-use help only, built from live data."""
    groq_api_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_api_key or groq_api_key == "your_groq_api_key_here":
        return jsonify({
                           "reply": "⚠️ Groq API key not configured. Please add your GROQ_API_KEY to sandbox.env and restart the server."}), 200

    data = request.get_json(force=True) or {}
    user_message = (data.get("message") or "").strip()
    history = data.get("history", [])  # [{"role": "user/assistant", "content": "..."}]

    # Basic input hygiene: reject empty input and cap length to prevent abuse
    if not user_message:
        return jsonify({"reply": "Please type a message."}), 200
    if len(user_message) > 1000:
        return jsonify({"reply": "That message is too long — please shorten it."}), 200

    try:
        client = Groq(api_key=groq_api_key)

        messages = [{"role": "system", "content": build_alx_system_prompt()}]
        # Include recent conversation history (capped, and only well-formed turns)
        for turn in history[-CHAT_HISTORY_LIMIT:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content.strip()[:2000]})
        messages.append({"role": "user", "content": user_message})

        completion = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            max_tokens=CHAT_MAX_TOKENS,
            temperature=0.7,
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        # Never leak raw exception details (API keys, stack traces) to the client
        print(f"[chat] Groq API error: {e}")
        reply = "Sorry, I'm having trouble responding right now. Please try again in a moment."

    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)