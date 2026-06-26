from flask import Flask, render_template, request, jsonify, session, redirect, send_file
import sqlite3
import os
import hmac
import hashlib
import base64
import datetime
from functools import wraps
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
import io
import csv
import subprocess
import sys

# ─── HARDWARE ID ─────────────────────────────────────────────────────────────
def get_hwid():
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid', shell=True, stderr=subprocess.DEVNULL).decode().split('\n')[1].strip()
        return hwid if hwid else str(__import__('uuid').getnode())
    except:
        return str(__import__('uuid').getnode())

# ─── PYINSTALLER PATH FIX ────────────────────────────────────────────────────
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

# ─── APP INIT ────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder=resource_path('templates'))
app.secret_key = 'restora_local_2026_xk9'

# ─── PATHS ───────────────────────────────────────────────────────────────────
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'restora.db')
LICENSE_PATH = os.path.join(BASE_DIR, 'license.dat')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.txt')
LICENSE_SECRET = "RESTORA_BILLZO_2026_SECRET_XK9"

# ─── CONFIG ──────────────────────────────────────────────────────────────────
def get_admin_credentials():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w') as f:
            f.write("admin_id=admin\nadmin_password=admin123\n")
    creds = {}
    with open(CONFIG_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                creds[k.strip()] = v.strip()
    return creds.get('admin_id', 'admin'), creds.get('admin_password', 'admin123')

# ─── LICENSE ─────────────────────────────────────────────────────────────────
def generate_key_for_date(expiry_date_str, hwid=''):
    payload = f"RESTORA|{expiry_date_str}|{hwid}"
    sig = hmac.new(LICENSE_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    b64 = base64.b32encode(sig).decode()[:16]
    return f"RSTR-{b64[0:4]}-{b64[4:8]}-{b64[8:12]}-{b64[12:16]}"

def verify_license_key(key):
    key = key.strip().upper()
    parts = key.split("-")
    if len(parts) != 5 or parts[0] != "RSTR":
        return False, "Invalid key format"
    today = datetime.date.today()
    hwid = get_hwid()
    for delta in range(0, 366 * 3):
        check_date = today + datetime.timedelta(days=delta)
        if generate_key_for_date(str(check_date), hwid) == key:
            return True, str(check_date)
    for delta in range(1, 366 * 2):
        check_date = today - datetime.timedelta(days=delta)
        if generate_key_for_date(str(check_date), hwid) == key:
            return True, str(check_date)
    return False, "Ye key is system ke liye nahi hai ya expired ho gayi"

def get_license_status():
    if not os.path.exists(LICENSE_PATH):
        return {'activated': False, 'valid': False, 'expiry': None, 'days_left': 0, 'expiring_soon': False, 'hwid_match': False}
    with open(LICENSE_PATH, 'r') as f:
        content = f.read().strip()
    parts = content.split('|')
    stored_expiry = parts[0]
    stored_hwid = parts[1] if len(parts) > 1 else None
    current_hwid = get_hwid()
    hwid_match = stored_hwid == current_hwid if stored_hwid else False
    try:
        expiry_date = datetime.date.fromisoformat(stored_expiry)
    except Exception:
        return {'activated': False, 'valid': False, 'expiry': None, 'days_left': 0, 'expiring_soon': False, 'hwid_match': False}
    today = datetime.date.today()
    days_left = (expiry_date - today).days
    valid = days_left >= 0 and hwid_match
    return {
        'activated': True, 'valid': valid, 'expiry': stored_expiry,
        'days_left': days_left, 'expiring_soon': valid and days_left <= 7, 'hwid_match': hwid_match
    }

# ─── DECORATORS ──────────────────────────────────────────────────────────────
def license_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        lic = get_license_status()
        if not lic['valid']:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'license_expired': True, 'msg': 'License expired'}), 403
            return redirect('/activate')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def waiter_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'waiter':
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def kitchen_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'kitchen':
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

# ─── DATABASE ────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def db_query(sql, params=None, fetch='auto'):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        result = None
        if fetch == 'auto':
            if cur.description:
                result = [dict(r) for r in cur.fetchall()]
        elif fetch == 'all':
            result = [dict(r) for r in cur.fetchall()]
        elif fetch == 'one':
            row = cur.fetchone()
            result = dict(row) if row else None
        elif fetch == 'lastrowid':
            result = cur.lastrowid
        conn.commit()
        return result
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_next_token_number():
    """Token number har din 101 se shuru hota hai, date change hone par reset ho jata hai."""
    row = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now','+5 hours','+30 minutes')", fetch='one')
    count = row['c'] if row else 0
    return 101 + count

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS restaurant_settings (
            id INTEGER PRIMARY KEY,
            gst_percentage REAL DEFAULT 5.0,
            gst_number TEXT,
            gst_enabled INTEGER DEFAULT 1,
            rest_name TEXT DEFAULT 'Restaurant',
            rest_phone TEXT DEFAULT '',
            rest_address TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
        );
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('waiter','kitchen','cashier')),
            pin TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_number TEXT NOT NULL UNIQUE,
            capacity INTEGER DEFAULT 4,
            status TEXT DEFAULT 'available'
        );
        CREATE TABLE IF NOT EXISTS menu_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER REFERENCES menu_categories(id),
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            is_available INTEGER DEFAULT 1,
            stock_kg REAL DEFAULT NULL,
            stock_updated_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id INTEGER REFERENCES tables(id),
            waiter_id INTEGER REFERENCES staff(id),
            cashier_id INTEGER REFERENCES staff(id),
            customer_name TEXT,
            customer_mobile TEXT,
            customer_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active' CHECK (status IN ('active','bill_pending','bill_generated','completed','cancelled')),
            payment_mode TEXT,
            subtotal REAL DEFAULT 0,
            gst_amount REAL DEFAULT 0,
            total_amount REAL DEFAULT 0,
            bill_number TEXT UNIQUE,
            bill_generated_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER REFERENCES orders(id),
            menu_item_id INTEGER REFERENCES menu_items(id),
            item_name TEXT,
            quantity INTEGER NOT NULL,
            quantity_kg REAL DEFAULT NULL,
            weight_label TEXT DEFAULT NULL,
            price REAL NOT NULL,
            special_instruction TEXT,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','ready','served'))
        );
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id INTEGER REFERENCES tables(id),
            customer_name TEXT NOT NULL,
            customer_phone TEXT,
            party_size INTEGER,
            reservation_date TEXT NOT NULL,
            reservation_time TEXT NOT NULL,
            special_requests TEXT,
            status TEXT DEFAULT 'confirmed',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            staff_name TEXT,
            role TEXT,
            login_at TEXT DEFAULT (datetime('now')),
            logout_at TEXT
        );
        CREATE TABLE IF NOT EXISTS data_retention_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type TEXT,
            records_deleted INTEGER DEFAULT 0,
            deleted_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'completed'
        );
        CREATE TABLE IF NOT EXISTS customer_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER REFERENCES orders(id),
            customer_phone TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS yearly_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER,
            month INTEGER,
            total_orders INTEGER,
            total_revenue REAL,
            avg_order REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER REFERENCES orders(id),
            cashier_id INTEGER REFERENCES staff(id),
            waiter_id INTEGER REFERENCES staff(id),
            amount_received REAL NOT NULL,
            payment_mode TEXT NOT NULL,
            status TEXT DEFAULT 'completed' CHECK (status IN ('pending','completed','returned')),
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """);
    db_query("INSERT OR IGNORE INTO restaurant_settings (id, gst_percentage, gst_number) VALUES (1, 5.0, '')")

    # Safe migration for existing databases
    try:
        db_query("ALTER TABLE menu_items ADD COLUMN stock_kg REAL DEFAULT NULL")
    except: pass
    try:
        db_query("ALTER TABLE menu_items ADD COLUMN stock_updated_at TEXT DEFAULT NULL")
    except: pass
    try:
        db_query("ALTER TABLE order_items ADD COLUMN quantity_kg REAL DEFAULT NULL")
    except: pass
    try:
        db_query("ALTER TABLE order_items ADD COLUMN weight_label TEXT DEFAULT NULL")
    except: pass
    try:
        db_query("ALTER TABLE menu_items ADD COLUMN gst_enabled INTEGER DEFAULT 1")
    except: pass
    try:
        db_query("ALTER TABLE restaurant_settings ADD COLUMN gst_enabled INTEGER DEFAULT 1")
    except: pass
    try:
        db_query("ALTER TABLE orders ADD COLUMN token_number INTEGER DEFAULT NULL")
    except: pass
    try:
        db_query("ALTER TABLE menu_categories ADD COLUMN top_pick INTEGER DEFAULT NULL")
    except: pass


    conn.commit()
    conn.close()

# ─── LICENSE ROUTES ──────────────────────────────────────────────────────────
@app.route('/activate', methods=['GET', 'POST'])
def activate():
    if request.method == 'POST':
        d = request.get_json()
        key = d.get('key', '').strip()
        ok, result = verify_license_key(key)
        if not ok:
            return jsonify({'ok': False, 'msg': result})
        expiry_date = datetime.date.fromisoformat(result)
        if expiry_date < datetime.date.today():
            return jsonify({'ok': False, 'msg': f'Ye key expire ho chuki hai ({result})'})
        with open(LICENSE_PATH, 'w') as f:
            f.write(f"{result}|{get_hwid()}")
        return jsonify({'ok': True, 'expiry': result})
    return render_template('activate.html')

@app.route('/api/license_status')
def license_status_api():
    return jsonify(get_license_status())

@app.route('/api/hwid')
def public_hwid():
    return jsonify({'hwid': get_hwid()})

@app.route('/api/admin/hwid')
def admin_hwid():
    return jsonify({'hwid': get_hwid()})

# ─── MAIN ROUTES ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    lic = get_license_status()
    if not lic['valid']:
        return redirect('/activate')
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
@license_required
def login():
    if request.method == 'POST':
        d = request.get_json()
        role = d.get('role')
        admin_id, admin_pwd = get_admin_credentials()
        if role == 'admin':
            if d.get('admin_id', '').strip() == admin_id and d.get('admin_password', '') == admin_pwd:
                session['role'] = 'admin'
                session['restaurant_name'] = 'Restaurant'
                return jsonify({'ok': True, 'redirect': '/admin/dashboard'})
            return jsonify({'ok': False, 'msg': 'Admin ID ya Password galat hai'})
        staff_name = d.get('staff_name', '').strip()
        staff_pin = d.get('staff_pin', '').strip()
        # staff = db_query("SELECT * FROM staff WHERE name=? AND pin=? AND role=? AND is_active=1", (staff_name, staff_pin, role), 'one')
        if role not in ['waiter', 'kitchen', 'cashier']:
            return jsonify({'ok': False, 'msg': 'Invalid role'})
        staff = db_query("SELECT * FROM staff WHERE name=? AND pin=? AND role=? AND is_active=1", (staff_name, staff_pin, role), 'one')


        if not staff:
            return jsonify({'ok': False, 'msg': 'Naam ya PIN galat hai'})
        log_id = db_query("INSERT INTO login_logs (staff_id, staff_name, role, login_at) VALUES (?,?,?,datetime('now','+5 hours','+30 minutes'))", (staff['id'], staff['name'], role), 'lastrowid')
        session['role'] = role
        session['staff_id'] = staff['id']
        session['staff_name'] = staff['name']
        session['log_id'] = log_id
        # if role == 'waiter':
        #     return jsonify({'ok': True, 'redirect': '/waiter/dashboard'})
        # return jsonify({'ok': True, 'redirect': '/kitchen/dashboard'})


        if role == 'waiter':
            return jsonify({'ok': True, 'redirect': '/waiter/dashboard'})
        elif role == 'kitchen':
            return jsonify({'ok': True, 'redirect': '/kitchen/dashboard'})
        elif role == 'cashier':
            return jsonify({'ok': True, 'redirect': '/cashier/dashboard'})
        

    return render_template('login.html')

@app.route('/api/staff_names')
@license_required
def get_staff_names():
    role = request.args.get('role', '')
    if not role:
        return jsonify([])
    rows = db_query("SELECT name FROM staff WHERE role=? AND is_active=1 ORDER BY name", (role,))
    return jsonify([r['name'] for r in rows])

@app.route('/logout')
def logout():
    log_id = session.get('log_id')
    if log_id:
        db_query("UPDATE login_logs SET logout_at=datetime('now','+5 hours','+30 minutes') WHERE id=?", (log_id,))
    session.clear()
    return redirect('/login')

# ─── ADMIN PANEL ─────────────────────────────────────────────────────────────
@app.route('/admin/dashboard')
@admin_required
@license_required
def admin_dashboard():
    return render_template('admin_dashboard.html')

@app.route('/api/admin/stats')
@admin_required
@license_required
def admin_stats():
    today = str(datetime.date.today())
    total_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at)=? AND status='completed'", (today,), 'one')['c']
    revenue = db_query("SELECT COALESCE(SUM(total_amount),0) as s FROM orders WHERE date(created_at)=? AND status='completed'", (today,), 'one')['s']
    active_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE status='active'", fetch='one')['c']
    tables_occ = db_query("SELECT COUNT(*) as c FROM tables WHERE status='occupied'", fetch='one')['c']
    lic = get_license_status()
    return jsonify({'today_orders': total_orders, 'today_revenue': float(revenue), 'active_orders': active_orders, 'tables_occupied': tables_occ, 'license': lic})

@app.route('/api/admin/weekly_revenue')
@admin_required
@license_required
def admin_weekly_revenue():
    rows = db_query("""SELECT date(created_at) as d, SUM(total_amount) as rev FROM orders WHERE status='completed' AND created_at >= datetime('now','-7 days') GROUP BY date(created_at) ORDER BY d""")
    return jsonify([{'date': r['d'], 'revenue': float(r['rev'])} for r in rows])

@app.route('/api/admin/staff', methods=['GET'])
@admin_required
@license_required
def admin_get_staff():
    return jsonify(db_query("SELECT * FROM staff ORDER BY role, name"))

@app.route('/api/admin/staff', methods=['POST'])
@admin_required
@license_required
def admin_add_staff():
    d = request.get_json()
    db_query("INSERT INTO staff (name, role, pin) VALUES (?,?,?)", (d['name'], d['role'], d['pin']))
    return jsonify({'ok': True})

@app.route('/api/admin/staff/<int:sid>', methods=['PUT'])
@admin_required
@license_required
def admin_edit_staff(sid):
    d = request.get_json()
    db_query("UPDATE staff SET name=?, role=?, pin=?, is_active=? WHERE id=?", (d['name'], d['role'], d['pin'], d.get('is_active', 1), sid))
    return jsonify({'ok': True})

@app.route('/api/admin/staff/<int:sid>', methods=['DELETE'])
@admin_required
@license_required
def admin_delete_staff(sid):
    db_query("UPDATE staff SET is_active=0 WHERE id=?", (sid,))
    return jsonify({'ok': True})

@app.route('/api/admin/tables', methods=['GET'])
@admin_required
@license_required
def admin_get_tables():
    return jsonify(db_query("SELECT * FROM tables ORDER BY table_number"))

@app.route('/api/admin/tables', methods=['POST'])
@admin_required
@license_required
def admin_add_table():
    d = request.get_json()
    db_query("INSERT INTO tables (table_number, capacity) VALUES (?,?)", (d['table_number'], d.get('capacity', 4)))
    return jsonify({'ok': True})

@app.route('/api/admin/tables/<int:tid>', methods=['DELETE'])
@admin_required
@license_required
def admin_delete_table(tid):
    db_query("DELETE FROM tables WHERE id=?", (tid,))
    return jsonify({'ok': True})

@app.route('/api/admin/categories', methods=['GET'])
@admin_required
@license_required
def admin_get_categories():
    return jsonify(db_query("SELECT * FROM menu_categories ORDER BY sort_order, name"))

@app.route('/api/admin/categories', methods=['POST'])
@admin_required
@license_required
def admin_add_category():
    d = request.get_json()
    db_query("INSERT INTO menu_categories (name, sort_order) VALUES (?,?)", (d['name'], d.get('sort_order', 0)))
    return jsonify({'ok': True})

@app.route('/api/admin/categories/<int:cid>', methods=['DELETE'])
@admin_required
@license_required
def admin_delete_category(cid):
    db_query("DELETE FROM menu_categories WHERE id=?", (cid,))
    return jsonify({'ok': True})

@app.route('/api/admin/menu', methods=['GET'])
@admin_required
@license_required
def admin_get_menu():
    return jsonify(db_query("""SELECT m.*, c.name as category_name FROM menu_items m LEFT JOIN menu_categories c ON m.category_id=c.id ORDER BY c.sort_order, m.name"""))

@app.route('/api/admin/menu', methods=['POST'])
@admin_required
@license_required
def admin_add_menu():
    d = request.get_json()
    db_query("INSERT INTO menu_items (category_id, name, description, price) VALUES (?,?,?,?)", (d['category_id'], d['name'], d.get('description', ''), d['price']))
    return jsonify({'ok': True})

@app.route('/api/admin/menu/<int:mid>', methods=['PUT'])
@admin_required
@license_required
def admin_edit_menu(mid):
    d = request.get_json()
    db_query("UPDATE menu_items SET name=?, description=?, price=?, category_id=?, is_available=? WHERE id=?", (d['name'], d.get('description', ''), d['price'], d['category_id'], d.get('is_available', 1), mid))
    return jsonify({'ok': True})

@app.route('/api/admin/menu/<int:mid>', methods=['DELETE'])
@admin_required
@license_required
def admin_delete_menu(mid):
    db_query("DELETE FROM menu_items WHERE id=?", (mid,))
    return jsonify({'ok': True})

# ─── STOCK MANAGEMENT ────────────────────────────────────────────────────────
@app.route('/api/admin/menu/<int:mid>/stock', methods=['PUT'])
@admin_required
@license_required
def admin_update_stock(mid):
    d = request.get_json()
    stock_kg = d.get('stock_kg')
    now_ist = f"datetime('now','+5 hours','+30 minutes')"
    db_query(f"UPDATE menu_items SET stock_kg=?, stock_updated_at={now_ist} WHERE id=?", (stock_kg, mid))
    return jsonify({'ok': True})

@app.route('/api/admin/stock_status')
@admin_required
@license_required
def admin_stock_status():
    """
    Stock status: item ka total stock (jo kabhi bhi add kiya gaya stock_updated_at se)
    minus jo bhi us date ke baad bika.
    Agar naya stock daala to us time ke baad se hi calculate karo.
    """
    rows = db_query("""
        SELECT m.id, m.name, c.name as category_name, m.price,
               m.stock_kg, m.stock_updated_at
        FROM menu_items m
        LEFT JOIN menu_categories c ON m.category_id=c.id
        ORDER BY c.sort_order, m.name
    """)
    result = []
    for r in rows:
        stock = r['stock_kg']
        updated_at = r['stock_updated_at']
        if stock is not None and updated_at:
            # Jab se stock daala, tab se kitna bika (quantity_kg use karo, agar null to quantity use karo)
            sold_row = db_query("""
                SELECT COALESCE(SUM(
                    CASE WHEN oi.quantity_kg IS NOT NULL AND oi.quantity_kg > 0
                         THEN oi.quantity_kg
                         ELSE 0
                    END
                ), 0) as sold_kg
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.id
                WHERE oi.menu_item_id = ?
                  AND o.status NOT IN ('cancelled')
                  AND o.created_at >= ?
            """, (r['id'], updated_at), 'one')
            sold_kg = float(sold_row['sold_kg'] or 0)
            remaining = round(stock - sold_kg, 3)
        else:
            sold_kg = 0
            remaining = None
        r['sold_kg'] = sold_kg
        r['remaining_kg'] = remaining
        result.append(r)
    return jsonify(result)

@app.route('/api/waiter/stock_status')
@waiter_required
@license_required
def waiter_stock_status():
    """Waiter ke liye sirf remaining stock dikhao (read-only)"""
    rows = db_query("""
        SELECT m.id, m.stock_kg, m.stock_updated_at
        FROM menu_items m
    """)
    result = {}
    for r in rows:
        stock = r['stock_kg']
        updated_at = r['stock_updated_at']
        if stock is not None and updated_at:
            sold_row = db_query("""
                SELECT COALESCE(SUM(CASE WHEN oi.quantity_kg IS NOT NULL AND oi.quantity_kg > 0 THEN oi.quantity_kg ELSE 0 END), 0) as sold_kg
                FROM order_items oi JOIN orders o ON oi.order_id=o.id
                WHERE oi.menu_item_id=? AND o.status NOT IN ('cancelled') AND o.created_at >= ?
            """, (r['id'], updated_at), 'one')
            remaining = round(stock - float(sold_row['sold_kg'] or 0), 3)
        else:
            remaining = None
        result[r['id']] = remaining
    return jsonify(result)

@app.route('/api/cashier/stock_status')
@license_required
def cashier_stock_status():
    if session.get('role') != 'cashier': return jsonify({})
    rows = db_query("SELECT m.id, m.stock_kg, m.stock_updated_at FROM menu_items m")
    result = {}
    for r in rows:
        stock = r['stock_kg']
        updated_at = r['stock_updated_at']
        if stock is not None and updated_at:
            sold_row = db_query("""
                SELECT COALESCE(SUM(CASE WHEN oi.quantity_kg IS NOT NULL AND oi.quantity_kg > 0 THEN oi.quantity_kg ELSE 0 END), 0) as sold_kg
                FROM order_items oi JOIN orders o ON oi.order_id=o.id
                WHERE oi.menu_item_id=? AND o.status NOT IN ('cancelled') AND o.created_at >= ?
            """, (r['id'], updated_at), 'one')
            remaining = round(stock - float(sold_row['sold_kg'] or 0), 3)
        else:
            remaining = None
        result[r['id']] = remaining
    return jsonify(result)

@app.route('/api/admin/reports/orders')
@admin_required
@license_required
def admin_orders_report():
    date_from = request.args.get('from', str(datetime.date.today()))
    date_to = request.args.get('to', str(datetime.date.today()))
    rows = db_query("""SELECT o.*, s.name as waiter_name, t.table_number FROM orders o LEFT JOIN staff s ON o.waiter_id=s.id LEFT JOIN tables t ON o.table_id=t.id WHERE date(o.created_at) BETWEEN ? AND ? ORDER BY o.created_at DESC""", (date_from, date_to))
    return jsonify(rows)

@app.route('/api/admin/reports/order/<int:oid>/items')
@admin_required
@license_required
def admin_order_items(oid):
    return jsonify(db_query("SELECT * FROM order_items WHERE order_id=?", (oid,)))

@app.route('/api/admin/login_logs')
@admin_required
@license_required
def admin_login_logs():
    return jsonify(db_query("SELECT * FROM login_logs ORDER BY login_at DESC LIMIT 100"))

@app.route('/api/admin/kitchen_settings', methods=['GET'])
@admin_required
@license_required
def get_kitchen_settings():
    return jsonify({'display_type': 'screen'})

@app.route('/api/admin/reservations', methods=['GET'])
@admin_required
@license_required
def admin_get_reservations():
    return jsonify(db_query("""SELECT r.*, t.table_number FROM reservations r LEFT JOIN tables t ON r.table_id=t.id ORDER BY r.reservation_date, r.reservation_time"""))

@app.route('/api/admin/reservations', methods=['POST'])
@admin_required
@license_required
def admin_add_reservation():
    d = request.get_json()
    db_query("INSERT INTO reservations (table_id, customer_name, customer_phone, party_size, reservation_date, reservation_time, special_requests) VALUES (?,?,?,?,?,?,?)", (d['table_id'], d['customer_name'], d.get('customer_phone', ''), d.get('party_size', 2), d['reservation_date'], d['reservation_time'], d.get('special_requests', '')))
    return jsonify({'ok': True})

@app.route('/api/admin/reservations/<int:resid>', methods=['PUT'])
@admin_required
@license_required
def admin_update_reservation(resid):
    d = request.get_json()
    db_query("UPDATE reservations SET status=? WHERE id=?", (d['status'], resid))
    return jsonify({'ok': True})

# @app.route('/api/admin/restaurant_info', methods=['GET'])
# @admin_required
# @license_required
# def admin_restaurant_info():
#     return jsonify({'name': session.get('restaurant_name', 'Restaurant'), 'phone': session.get('restaurant_phone', ''), 'address': session.get('restaurant_address', ''), 'gst_number': session.get('restaurant_gst', '')})



@app.route('/api/admin/restaurant_info', methods=['GET'])
@admin_required
@license_required
def admin_restaurant_info():
    s = db_query("SELECT * FROM restaurant_settings WHERE id=1", fetch='one')
    return jsonify({
        'name': s.get('rest_name', 'Restaurant') if s else 'Restaurant',
        'phone': s.get('rest_phone', '') if s else '',
        'address': s.get('rest_address', '') if s else '',
        'gst_number': s.get('gst_number', '') if s else ''
    })


# @app.route('/api/admin/restaurant_info', methods=['PUT'])
# @admin_required
# @license_required
# def admin_update_restaurant():
#     d = request.get_json()
#     session['restaurant_name'] = d.get('name', 'Restaurant')
#     session['restaurant_phone'] = d.get('phone', '')
#     session['restaurant_address'] = d.get('address', '')
#     session['restaurant_gst'] = d.get('gst_number', '')
#     return jsonify({'ok': True})



@app.route('/api/admin/restaurant_info', methods=['PUT'])
@admin_required
@license_required
def admin_update_restaurant():
    d = request.get_json()
    name = d.get('name', 'Restaurant')
    session['restaurant_name'] = name
    session['restaurant_phone'] = d.get('phone', '')
    session['restaurant_address'] = d.get('address', '')
    session['restaurant_gst'] = d.get('gst_number', '')
    db_query("UPDATE restaurant_settings SET rest_name=?, rest_phone=?, rest_address=?, gst_number=? WHERE id=1",
             (name, d.get('phone', ''), d.get('address', ''), d.get('gst_number', '')))
    return jsonify({'ok': True})

@app.route('/api/admin/gst_settings', methods=['GET'])
@admin_required
@license_required
def get_gst_settings():
    settings = db_query("SELECT gst_percentage, gst_number, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    return jsonify(settings or {'gst_percentage': 5.0, 'gst_number': '', 'gst_enabled': 1})

@app.route('/api/public/gst_settings')
@license_required
def public_gst_settings():
    """Waiter/cashier ke liye GST settings — no admin required"""
    settings = db_query("SELECT gst_percentage, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    return jsonify(settings or {'gst_percentage': 5.0, 'gst_enabled': 1})

@app.route('/api/admin/gst_settings', methods=['PUT'])
@admin_required
@license_required
def update_gst_settings():
    d = request.get_json()
    gst_pct = float(d.get('gst_percentage', 5.0))
    gst_num = d.get('gst_number', '')
    if 'gst_enabled' in d:
        gst_enabled = 1 if d.get('gst_enabled') else 0
        db_query("UPDATE restaurant_settings SET gst_percentage=?, gst_number=?, gst_enabled=? WHERE id=1", (gst_pct, gst_num, gst_enabled))
    else:
        db_query("UPDATE restaurant_settings SET gst_percentage=?, gst_number=? WHERE id=1", (gst_pct, gst_num))
    return jsonify({'ok': True})



@app.route('/api/admin/change_password', methods=['POST'])
@admin_required
@license_required
def admin_change_password():
    d = request.get_json()
    _, current_pwd = get_admin_credentials()
    if d.get('current_password') != current_pwd:
        return jsonify({'ok': False, 'msg': 'Current password galat hai'})
    admin_id, _ = get_admin_credentials()
    with open(CONFIG_PATH, 'w') as f:
        f.write(f"admin_id={admin_id}\nadmin_password={d['new_password']}\n")
    return jsonify({'ok': True})

@app.route('/api/admin/renew_license', methods=['POST'])
@admin_required
@license_required
def admin_renew_license():
    d = request.get_json()
    key = d.get('key', '').strip()
    ok, result = verify_license_key(key)
    if not ok:
        return jsonify({'ok': False, 'msg': result})
    expiry_date = datetime.date.fromisoformat(result)
    if expiry_date < datetime.date.today():
        return jsonify({'ok': False, 'msg': f'Ye key expire ho chuki hai ({result})'})
    with open(LICENSE_PATH, 'w') as f:
        f.write(f"{result}|{get_hwid()}")
    return jsonify({'ok': True, 'expiry': result})

@app.route('/api/admin/analytics')
@admin_required
@license_required
def admin_analytics():
    date_from = request.args.get('from', str(datetime.date.today() - datetime.timedelta(days=30)))
    date_to = request.args.get('to', str(datetime.date.today()))
    stats = db_query("SELECT COUNT(*) as total_orders, COALESCE(SUM(total_amount),0) as total_revenue, COALESCE(AVG(total_amount),0) as avg_order FROM orders WHERE status='completed' AND date(created_at) BETWEEN ? AND ?", (date_from, date_to), 'one')
    daily = db_query("SELECT date(created_at) as d, SUM(total_amount) as rev FROM orders WHERE status='completed' AND date(created_at) BETWEEN ? AND ? GROUP BY date(created_at) ORDER BY d", (date_from, date_to))
    top_items = db_query("SELECT item_name, SUM(quantity) as total_qty, SUM(price*quantity) as total_rev FROM order_items oi JOIN orders o ON oi.order_id=o.id WHERE o.status='completed' AND date(o.created_at) BETWEEN ? AND ? GROUP BY item_name ORDER BY total_qty DESC LIMIT 5", (date_from, date_to))
    payment = db_query("SELECT payment_mode, COUNT(*) as count, SUM(total_amount) as total FROM orders WHERE status='completed' AND date(created_at) BETWEEN ? AND ? GROUP BY payment_mode", (date_from, date_to))
    yearly = db_query("SELECT strftime('%Y-%m', created_at) as ym, COUNT(*) as orders, SUM(total_amount) as rev FROM orders WHERE status='completed' GROUP BY ym ORDER BY ym DESC LIMIT 12")
    detailed = db_query("SELECT o.bill_number, oi.item_name, oi.quantity, oi.price, (oi.quantity*oi.price) as total_amount, o.payment_mode, o.created_at FROM order_items oi JOIN orders o ON oi.order_id=o.id WHERE o.status='completed' AND date(o.created_at) BETWEEN ? AND ? ORDER BY o.created_at DESC", (date_from, date_to))
    return jsonify({'total_orders': stats['total_orders'], 'total_revenue': float(stats['total_revenue']), 'avg_order': float(stats['avg_order']), 'top_item': top_items[0]['item_name'] if top_items else '—', 'daily_revenue': [{'date': r['d'], 'revenue': float(r['rev'])} for r in daily], 'top_items': top_items, 'payment_breakdown': payment, 'yearly_data': yearly, 'detailed_orders': detailed})

@app.route('/api/admin/data_retention_status')
@admin_required
@license_required
def data_retention_status():
    logs = db_query("SELECT * FROM data_retention_log ORDER BY deleted_at DESC LIMIT 10")
    today = datetime.date.today()
    next_cleanup = datetime.date(today.year + 1, 1, 1) if today.month == 12 else datetime.date(today.year, today.month + 1, 1)
    return jsonify({'next_cleanup': str(next_cleanup), 'cleanup_logs': logs})

@app.route('/api/admin/export_retention_data')
@admin_required
@license_required
def export_retention_data():
    cutoff = datetime.date.today() - datetime.timedelta(days=90)
    rows = db_query("SELECT o.bill_number, o.customer_name, cd.customer_phone, oi.item_name, oi.quantity, oi.price, o.total_amount, o.payment_mode, o.created_at FROM orders o LEFT JOIN customer_data cd ON o.id=cd.order_id LEFT JOIN order_items oi ON o.id=oi.order_id WHERE date(o.created_at) < ? ORDER BY o.created_at", (str(cutoff),))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Bill No.', 'Customer', 'Phone', 'Item', 'Qty', 'Price', 'Total', 'Payment', 'Date'])
    for r in rows:
        writer.writerow([r['bill_number'], r['customer_name'], r['customer_phone'], r['item_name'], r['quantity'], r['price'], r['total_amount'], r['payment_mode'], r['created_at']])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name=f'retention_data_{datetime.date.today()}.csv')

@app.route('/api/admin/manual_cleanup', methods=['POST'])
@admin_required
@license_required
def manual_cleanup():
    cutoff = datetime.date.today() - datetime.timedelta(days=90)
    old_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) < ?", (str(cutoff),), 'one')['c']
    old_customers = db_query("SELECT COUNT(*) as c FROM customer_data WHERE date(created_at) < ?", (str(cutoff),), 'one')['c']
    db_query("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE date(created_at) < ?)", (str(cutoff),))
    db_query("DELETE FROM orders WHERE date(created_at) < ?", (str(cutoff),))
    db_query("DELETE FROM customer_data WHERE date(created_at) < ?", (str(cutoff),))
    db_query("INSERT INTO data_retention_log (data_type, records_deleted) VALUES (?,?)", ('old_orders', old_orders))
    db_query("INSERT INTO data_retention_log (data_type, records_deleted) VALUES (?,?)", ('customer_data', old_customers))
    return jsonify({'ok': True, 'msg': f'Deleted {old_orders} orders and {old_customers} customer records'})

# ─── WAITER PANEL ────────────────────────────────────────────────────────────
@app.route('/waiter/dashboard')
@waiter_required
@license_required
def waiter_dashboard():
    return render_template('waiter_dashboard.html')

@app.route('/api/waiter/tables')
@waiter_required
@license_required
def waiter_tables():
    return jsonify(db_query("SELECT * FROM tables ORDER BY table_number"))

@app.route('/api/waiter/menu')
@waiter_required
@license_required
def waiter_menu():
    # return jsonify(db_query("SELECT m.*, c.name as category_name FROM menu_items m LEFT JOIN menu_categories c ON m.category_id=c.id WHERE m.is_available=1 ORDER BY c.sort_order, m.name"))
    return jsonify(db_query("SELECT m.*, c.name as category_name, c.top_pick as category_top_pick FROM menu_items m LEFT JOIN menu_categories c ON m.category_id=c.id WHERE m.is_available=1 ORDER BY c.sort_order, m.name"))
# @app.route('/api/waiter/place_order', methods=['POST'])
# @waiter_required
# @license_required
# def waiter_place_order():
#     d = request.get_json()
#     items = d.get('items', [])
#     if not items:
#         return jsonify({'ok': False, 'msg': 'No items'})
#     subtotal = sum(i['price'] * i['quantity'] for i in items)
#     # Fetch GST from settings
#     settings = db_query("SELECT gst_percentage FROM restaurant_settings WHERE id=1", fetch='one')
#     gst_pct = float(settings['gst_percentage']) if settings else 5.0
#     gst = round(subtotal * (gst_pct / 100), 2)
#     total = subtotal + gst
#     bill_no = f"BILL-{int(datetime.datetime.now().timestamp())}"
#     # order_id = db_query("INSERT INTO orders (table_id, waiter_id, customer_name, customer_mobile, customer_count, subtotal, gst_amount, total_amount, bill_number, status) VALUES (?,?,?,?,?,?,?,?,?,?)", (d['table_id'], session['staff_id'], d.get('customer_name', ''), d.get('customer_mobile', ''), d.get('customer_count', 1), subtotal, gst, total, bill_no, 'bill_pending'), 'lastrowid')
#     order_id = db_query("INSERT INTO orders (table_id, waiter_id, customer_name, customer_mobile, customer_count, subtotal, gst_amount, total_amount, bill_number, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','+5 hours','+30 minutes'))", (d['table_id'], session['staff_id'], d.get('customer_name', ''), d.get('customer_mobile', ''), d.get('customer_count', 1), subtotal, gst, total, bill_no, 'active'), 'lastrowid')
#     if d.get('customer_mobile'):
#         db_query("INSERT INTO customer_data (order_id, customer_phone) VALUES (?,?)", (order_id, d.get('customer_mobile')))
#     for item in items:
#         db_query("INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, price, special_instruction) VALUES (?,?,?,?,?,?)", (order_id, item['id'], item['name'], item['quantity'], item['price'], item.get('special_instruction', '')))
#     db_query("UPDATE tables SET status='occupied' WHERE id=?", (d['table_id'],))
#     return jsonify({'ok': True, 'order_id': order_id})




@app.route('/api/waiter/place_order', methods=['POST'])
@waiter_required
@license_required
def waiter_place_order():
    d = request.get_json()
    items = d.get('items', [])
    if not items:
        return jsonify({'ok': False, 'msg': 'No items'})
    subtotal = sum(i['price'] * i['quantity'] for i in items)
    settings = db_query("SELECT gst_percentage, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    gst_pct = float(settings['gst_percentage']) if settings else 5.0
    gst_on = bool(settings['gst_enabled']) if settings and settings['gst_enabled'] is not None else True
    gst = round(subtotal * (gst_pct / 100), 2) if gst_on else 0.0
    total = subtotal + gst
    bill_no = f"BILL-{int(datetime.datetime.now().timestamp())}"
    token_no = get_next_token_number()
    is_walkin = d.get('is_walkin', False)
    table_id = None if is_walkin else d.get('table_id')
    if is_walkin:
        today = str(datetime.date.today())
        count_row = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at)=? AND customer_name LIKE 'WK-%'", (today,), 'one')
        walkin_num = (count_row['c'] if count_row else 0) + 1
        walkin_label = f"WK-{walkin_num:02d}"
        customer_name = d.get('customer_name', '') or walkin_label
    else:
        customer_name = d.get('customer_name', '')
    order_id = db_query("INSERT INTO orders (table_id, waiter_id, customer_name, customer_mobile, customer_count, subtotal, gst_amount, total_amount, bill_number, token_number, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now','+5 hours','+30 minutes'))", (table_id, session['staff_id'], customer_name, d.get('customer_mobile', ''), d.get('customer_count', 1), subtotal, gst, total, bill_no, token_no, 'active'), 'lastrowid')
    if d.get('customer_mobile'):
        db_query("INSERT INTO customer_data (order_id, customer_phone) VALUES (?,?)", (order_id, d.get('customer_mobile')))
    for item in items:
        db_query("INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, quantity_kg, weight_label, price, special_instruction) VALUES (?,?,?,?,?,?,?,?)",
            (order_id, item['id'], item['name'], item['quantity'], item.get('quantity_kg'), item.get('weight_label',''), item['price'], item.get('special_instruction', '')))
    if table_id:
        db_query("UPDATE tables SET status='occupied' WHERE id=?", (table_id,))
    return jsonify({'ok': True, 'order_id': order_id, 'token_number': token_no})



@app.route('/api/waiter/my_orders')
@waiter_required
@license_required
def waiter_my_orders():
    # rows = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.waiter_id=? AND o.status='active' ORDER BY o.created_at DESC", (session['staff_id'],))
    # rows = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.waiter_id=? AND o.status IN ('active','bill_pending','bill_generated') ORDER BY o.created_at DESC", (session['staff_id'],))


    # rows = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.waiter_id=? AND o.status='active' ORDER BY o.created_at DESC", (session['staff_id'],))


    rows = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.waiter_id=? AND o.status IN ('active','bill_pending') ORDER BY o.created_at DESC", (session['staff_id'],))


    result = []
    for r in rows:
        r['items'] = db_query("SELECT * FROM order_items WHERE order_id=?", (r['id'],))
        result.append(r)
    return jsonify(result)

@app.route('/api/waiter/add_items', methods=['POST'])
@waiter_required
@license_required
def waiter_add_items():
    d = request.get_json()
    order_id = d['order_id']
    for item in d.get('items', []):
        db_query("INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, quantity_kg, weight_label, price, special_instruction) VALUES (?,?,?,?,?,?,?,?)",
            (order_id, item['id'], item['name'], item['quantity'], item.get('quantity_kg'), item.get('weight_label',''), item['price'], item.get('special_instruction', '')))
    subtotal = db_query("SELECT COALESCE(SUM(price*quantity),0) as s FROM order_items WHERE order_id=?", (order_id,), 'one')['s']
    settings = db_query("SELECT gst_percentage, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    gst_pct = float(settings['gst_percentage']) if settings else 5.0
    gst_on = bool(settings['gst_enabled']) if settings and settings['gst_enabled'] is not None else True
    gst = round(float(subtotal) * (gst_pct / 100), 2) if gst_on else 0.0
    db_query("UPDATE orders SET subtotal=?, gst_amount=?, total_amount=? WHERE id=?", (subtotal, gst, float(subtotal) + gst, order_id))
    return jsonify({'ok': True})

@app.route('/api/waiter/mark_served', methods=['POST'])
@waiter_required
@license_required
def waiter_mark_served():
    d = request.get_json()
    db_query("UPDATE order_items SET status='served' WHERE order_id=?", (d['order_id'],))
    return jsonify({'ok': True})

@app.route('/api/waiter/cancel_order', methods=['POST'])
@waiter_required
@license_required
def waiter_cancel_order():
    d = request.get_json()
    oid = d['order_id']
    order = db_query("SELECT * FROM orders WHERE id=?", (oid,), 'one')
    db_query("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    if order:
        db_query("UPDATE tables SET status='available' WHERE id=?", (order['table_id'],))
    return jsonify({'ok': True})

# @app.route('/api/waiter/generate_bill', methods=['POST'])
# @waiter_required
# @license_required
# def waiter_generate_bill():
#     d = request.get_json()
#     oid = d['order_id']
#     payment_mode = d.get('payment_mode', 'cash')
#     db_query("UPDATE orders SET status='completed', payment_mode=?, completed_at=datetime('now') WHERE id=?", (payment_mode, oid))
#     order = db_query("SELECT * FROM orders WHERE id=?", (oid,), 'one')
#     if order:
#         db_query("UPDATE tables SET status='available' WHERE id=?", (order['table_id'],))
#     return jsonify({'ok': True, 'bill_number': order['bill_number'] if order else '', 'order': order})




@app.route('/api/waiter/generate_bill', methods=['POST'])
@waiter_required
@license_required
def waiter_generate_bill():
    d = request.get_json()
    oid = d['order_id']
    order = db_query("SELECT * FROM orders WHERE id=?", (oid,), 'one')
    if not order:
        return jsonify({'ok': False, 'msg': 'Order not found'})
    db_query("UPDATE orders SET status='bill_pending', bill_generated_at=datetime('now','+5 hours','+30 minutes') WHERE id=?", (oid,))
    return jsonify({'ok': True, 'bill_number': order['bill_number'], 'order': order})




# @app.route('/api/waiter/bill_data/<int:oid>')
# @waiter_required
# @license_required
# def waiter_bill_data(oid):
#     order = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.id=?", (oid,), 'one')
#     items = db_query("SELECT * FROM order_items WHERE order_id=?", (oid,))
#     return jsonify({'order': order, 'items': items, 'restaurant': {'name': session.get('restaurant_name', 'Restaurant'), 'phone': session.get('restaurant_phone', ''), 'address': session.get('restaurant_address', ''), 'gst_number': session.get('restaurant_gst', '')}})



@app.route('/api/waiter/bill_data/<int:oid>')
@waiter_required
@license_required
def waiter_bill_data(oid):
    order = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.id=?", (oid,), 'one')
    items = db_query("SELECT * FROM order_items WHERE order_id=?", (oid,))
    settings = db_query("SELECT * FROM restaurant_settings WHERE id=1", fetch='one')
    return jsonify({
        'order': order,
        'items': items,
        'restaurant': {
            'name': settings.get('rest_name', 'Restaurant') if settings else 'Restaurant',
            'phone': settings.get('rest_phone', '') if settings else '',
            'address': settings.get('rest_address', '') if settings else '',
            'gst_number': settings.get('gst_number', '') if settings else '',
            'gst_percentage': float(settings.get('gst_percentage', 5.0)) if settings else 5.0,
            'gst_enabled': bool(settings.get('gst_enabled', 1)) if settings else True
        }
    })



# ─── KITCHEN PANEL ───────────────────────────────────────────────────────────
@app.route('/kitchen/dashboard')
@kitchen_required
@license_required
def kitchen_dashboard():
    return render_template('kitchen_dashboard.html')



# # ─── CASHIER PANEL ───────────────────────────────────────────────────────────
# @app.route('/cashier/dashboard')
# def cashier_required(f):
#     @wraps(f)
#     def decorated(*args, **kwargs):
#         if session.get('role') != 'cashier':
#             return redirect('/login')
#         return f(*args, **kwargs)
#     return decorated

# @app.route('/cashier/dashboard')
# @license_required
# def cashier_dashboard():
#     if session.get('role') != 'cashier':
#         return redirect('/login')
#     return render_template('cashier_dashboard.html')



# ─── CASHIER PANEL ───────────────────────────────────────────────────────────
def cashier_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'cashier':
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/cashier/dashboard')
@cashier_required
@license_required
def cashier_dashboard():
    return render_template('cashier_dashboard.html')


@app.route('/api/cashier/tables')
@license_required
def cashier_tables():
    if session.get('role') != 'cashier': return jsonify([])
    return jsonify(db_query("SELECT * FROM tables ORDER BY table_number"))

@app.route('/api/cashier/menu')
@license_required
def cashier_menu():
    if session.get('role') != 'cashier': return jsonify([])
    return jsonify(db_query("SELECT m.*, c.name as category_name FROM menu_items m LEFT JOIN menu_categories c ON m.category_id=c.id WHERE m.is_available=1 ORDER BY c.sort_order, m.name"))

# @app.route('/api/cashier/place_order', methods=['POST'])
# @license_required
# def cashier_place_order():
#     if session.get('role') != 'cashier': return jsonify({'ok': False, 'msg': 'Unauthorized'})
#     d = request.get_json()
#     items = d.get('items', [])
#     if not items: return jsonify({'ok': False, 'msg': 'No items'})
#     subtotal = sum(i['price'] * i['quantity'] for i in items)
#     settings = db_query("SELECT gst_percentage FROM restaurant_settings WHERE id=1", fetch='one')
#     gst_pct = float(settings['gst_percentage']) if settings else 5.0
#     gst = round(subtotal * (gst_pct / 100), 2)
#     total = subtotal + gst
#     bill_no = f"BILL-{int(datetime.datetime.now().timestamp())}"
#     order_id = db_query("INSERT INTO orders (table_id, waiter_id, customer_name, customer_mobile, customer_count, subtotal, gst_amount, total_amount, bill_number, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','+5 hours','+30 minutes'))",
#         (d['table_id'], session['staff_id'], d.get('customer_name',''), d.get('customer_mobile',''), d.get('customer_count',1), subtotal, gst, total, bill_no, 'active'), 'lastrowid')
#     if d.get('customer_mobile'):
#         db_query("INSERT INTO customer_data (order_id, customer_phone) VALUES (?,?)", (order_id, d.get('customer_mobile')))
#     for item in items:
#         db_query("INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, price, special_instruction) VALUES (?,?,?,?,?,?)",
#             (order_id, item['id'], item['name'], item['quantity'], item['price'], item.get('special_instruction','')))
#     db_query("UPDATE tables SET status='occupied' WHERE id=?", (d['table_id'],))
#     return jsonify({'ok': True, 'order_id': order_id})




@app.route('/api/cashier/place_order', methods=['POST'])
@license_required
def cashier_place_order():
    if session.get('role') != 'cashier': return jsonify({'ok': False, 'msg': 'Unauthorized'})
    d = request.get_json()
    items = d.get('items', [])
    if not items: return jsonify({'ok': False, 'msg': 'No items'})
    subtotal = sum(i['price'] * i['quantity'] for i in items)
    settings = db_query("SELECT gst_percentage, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    gst_pct = float(settings['gst_percentage']) if settings else 5.0
    gst_on = bool(settings['gst_enabled']) if settings and settings['gst_enabled'] is not None else True
    gst = round(subtotal * (gst_pct / 100), 2) if gst_on else 0.0
    total = subtotal + gst
    bill_no = f"BILL-{int(datetime.datetime.now().timestamp())}"
    token_no = get_next_token_number()
    is_walkin = d.get('is_walkin', False)
    table_id = None if is_walkin else d.get('table_id')
    if is_walkin:
        today = str(datetime.date.today())
        count_row = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at)=? AND customer_name LIKE 'WK-%'", (today,), 'one')
        walkin_num = (count_row['c'] if count_row else 0) + 1
        walkin_label = f"WK-{walkin_num:02d}"
        customer_name = d.get('customer_name', '') or walkin_label
    else:
        customer_name = d.get('customer_name', '')
    order_id = db_query("INSERT INTO orders (table_id, waiter_id, customer_name, customer_mobile, customer_count, subtotal, gst_amount, total_amount, bill_number, token_number, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now','+5 hours','+30 minutes'))",
        (table_id, session['staff_id'], customer_name, d.get('customer_mobile',''), d.get('customer_count',1), subtotal, gst, total, bill_no, token_no, 'active'), 'lastrowid')
    if d.get('customer_mobile'):
        db_query("INSERT INTO customer_data (order_id, customer_phone) VALUES (?,?)", (order_id, d.get('customer_mobile')))
    for item in items:
        db_query("INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, quantity_kg, weight_label, price, special_instruction) VALUES (?,?,?,?,?,?,?,?)",
            (order_id, item['id'], item['name'], item['quantity'], item.get('quantity_kg'), item.get('weight_label',''), item['price'], item.get('special_instruction','')))
    if table_id:
        db_query("UPDATE tables SET status='occupied' WHERE id=?", (table_id,))
    return jsonify({'ok': True, 'order_id': order_id, 'token_number': token_no})




@app.route('/api/cashier/my_orders')
@license_required
def cashier_my_orders():
    if session.get('role') != 'cashier': return jsonify([])
    rows = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.waiter_id=? AND o.status IN ('active','bill_pending') ORDER BY o.created_at DESC", (session['staff_id'],))
    result = []
    for r in rows:
        r['items'] = db_query("SELECT * FROM order_items WHERE order_id=?", (r['id'],))
        result.append(r)
    return jsonify(result)

@app.route('/api/cashier/add_items', methods=['POST'])
@license_required
def cashier_add_items():
    if session.get('role') != 'cashier': return jsonify({'ok': False, 'msg': 'Unauthorized'})
    d = request.get_json()
    order_id = d['order_id']
    for item in d.get('items', []):
        db_query("INSERT INTO order_items (order_id, menu_item_id, item_name, quantity, quantity_kg, weight_label, price, special_instruction) VALUES (?,?,?,?,?,?,?,?)",
            (order_id, item['id'], item['name'], item['quantity'], item.get('quantity_kg'), item.get('weight_label',''), item['price'], item.get('special_instruction','')))
    subtotal = db_query("SELECT COALESCE(SUM(price*quantity),0) as s FROM order_items WHERE order_id=?", (order_id,), 'one')['s']
    settings = db_query("SELECT gst_percentage, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    gst_pct = float(settings['gst_percentage']) if settings else 5.0
    gst_on = bool(settings['gst_enabled']) if settings and settings['gst_enabled'] is not None else True
    gst = round(float(subtotal) * (gst_pct / 100), 2) if gst_on else 0.0
    db_query("UPDATE orders SET subtotal=?, gst_amount=?, total_amount=? WHERE id=?", (subtotal, gst, float(subtotal)+gst, order_id))
    return jsonify({'ok': True})

@app.route('/api/cashier/mark_served', methods=['POST'])
@license_required
def cashier_mark_served():
    if session.get('role') != 'cashier': return jsonify({'ok': False, 'msg': 'Unauthorized'})
    d = request.get_json()
    db_query("UPDATE order_items SET status='served' WHERE order_id=?", (d['order_id'],))
    return jsonify({'ok': True})

@app.route('/api/cashier/cancel_order', methods=['POST'])
@license_required
def cashier_cancel_order():
    if session.get('role') != 'cashier': return jsonify({'ok': False, 'msg': 'Unauthorized'})
    d = request.get_json()
    oid = d['order_id']
    order = db_query("SELECT * FROM orders WHERE id=?", (oid,), 'one')
    db_query("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    if order: db_query("UPDATE tables SET status='available' WHERE id=?", (order['table_id'],))
    return jsonify({'ok': True})

@app.route('/api/cashier/pending_bills')
@license_required
def cashier_pending_bills():
    if session.get('role') != 'cashier':
        return jsonify([])
    rows = db_query("""SELECT o.*, t.table_number, s.name as waiter_name FROM orders o 
        LEFT JOIN tables t ON o.table_id=t.id 
        LEFT JOIN staff s ON o.waiter_id=s.id 
        WHERE o.status='bill_pending' ORDER BY o.created_at ASC""")
    result = []
    for r in rows:
        r['items'] = db_query("SELECT * FROM order_items WHERE order_id=?", (r['id'],))
        result.append(r)
    return jsonify(result)

@app.route('/api/cashier/generate_bill', methods=['POST'])
@license_required
def cashier_generate_bill():
    if session.get('role') != 'cashier':
        return jsonify({'ok': False, 'msg': 'Unauthorized'})
    d = request.get_json()
    oid = d['order_id']
    payment_mode = d.get('payment_mode', 'cash')
    order = db_query("SELECT * FROM orders WHERE id=?", (oid,), 'one')
    if not order:
        return jsonify({'ok': False, 'msg': 'Order not found'})
    
    # Fetch current GST from settings
    settings = db_query("SELECT gst_percentage, gst_enabled FROM restaurant_settings WHERE id=1", fetch='one')
    gst_pct = float(settings['gst_percentage']) if settings else 5.0
    gst_on = bool(settings['gst_enabled']) if settings and settings['gst_enabled'] is not None else True
    subtotal = float(order['subtotal'])
    gst = round(subtotal * (gst_pct / 100), 2) if gst_on else 0.0
    
    db_query("UPDATE orders SET status='bill_generated', payment_mode=?, gst_amount=?, total_amount=?, bill_generated_at=datetime('now','+5 hours','+30 minutes'), cashier_id=? WHERE id=?", 
        (payment_mode, gst, subtotal + gst, session['staff_id'], oid))
    
    return jsonify({'ok': True, 'bill_number': order['bill_number']})

@app.route('/api/cashier/confirm_payment', methods=['POST'])
@license_required
def cashier_confirm_payment():
    if session.get('role') != 'cashier':
        return jsonify({'ok': False, 'msg': 'Unauthorized'})
    d = request.get_json()
    oid = d['order_id']
    amount = float(d.get('amount_received', 0))
    payment_mode = d.get('payment_mode', 'cash')
    order = db_query("SELECT * FROM orders WHERE id=?", (oid,), 'one')
    if not order:
        return jsonify({'ok': False, 'msg': 'Order not found'})
    
    # db_query("UPDATE orders SET status='completed', completed_at=datetime('now') WHERE id=?", (oid,))
    # db_query("INSERT INTO payments (order_id, cashier_id, waiter_id, amount_received, payment_mode) VALUES (?,?,?,?,?)", 
    #     (oid, session['staff_id'], order['waiter_id'], amount, payment_mode))



    db_query("UPDATE orders SET status='completed', completed_at=datetime('now','+5 hours','+30 minutes'), payment_mode=? WHERE id=?", (payment_mode, oid))
    db_query("INSERT INTO payments (order_id, cashier_id, waiter_id, amount_received, payment_mode, created_at) VALUES (?,?,?,?,?,datetime('now','+5 hours','+30 minutes'))", 
        (oid, session['staff_id'], order['waiter_id'], amount, payment_mode))
    
    
    
    if order['table_id']:
        db_query("UPDATE tables SET status='available' WHERE id=?", (order['table_id'],))
    
    return jsonify({'ok': True})

@app.route('/api/cashier/bill_data/<int:oid>')
@license_required
def cashier_bill_data(oid):
    if session.get('role') != 'cashier':
        return jsonify({'ok': False, 'msg': 'Unauthorized'})
    order = db_query("SELECT o.*, t.table_number FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.id=?", (oid,), 'one')
    items = db_query("SELECT * FROM order_items WHERE order_id=?", (oid,))
    settings = db_query("SELECT * FROM restaurant_settings WHERE id=1", fetch='one')
    return jsonify({
        'order': order,
        'items': items,
        'restaurant': {
            'name': settings.get('rest_name', 'Restaurant') if settings else 'Restaurant',
            'phone': settings.get('rest_phone', '') if settings else '',
            'address': settings.get('rest_address', '') if settings else '',
            'gst_number': settings.get('gst_number', '') if settings else '',
            'gst_percentage': float(settings.get('gst_percentage', 5.0)) if settings else 5.0,
            'gst_enabled': bool(settings.get('gst_enabled', 1)) if settings else True
        }
    })

@app.route('/api/admin/export_excel')
@admin_required
@license_required
def export_excel():
    date_from = request.args.get('from', str(datetime.date.today()))
    date_to = request.args.get('to', str(datetime.date.today()))
    orders = db_query("""
        SELECT o.id, o.bill_number, t.table_number, s.name as waiter_name,
               o.customer_name, o.customer_mobile, o.customer_count,
               o.subtotal, o.gst_amount, o.total_amount,
               o.payment_mode, o.status,
               strftime('%d/%m/%Y %H:%M', o.created_at) as order_time,
               strftime('%d/%m/%Y %H:%M', o.completed_at) as completed_time
        FROM orders o
        LEFT JOIN staff s ON o.waiter_id = s.id
        LEFT JOIN tables t ON o.table_id = t.id
        WHERE date(o.created_at) BETWEEN ? AND ?
        ORDER BY o.created_at DESC
    """, (date_from, date_to))
    items_all = db_query("""
        SELECT oi.order_id, oi.item_name, oi.quantity, oi.quantity_kg, oi.price,
               (oi.quantity * oi.price) as total, oi.special_instruction, oi.status
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.id
        WHERE date(o.created_at) BETWEEN ? AND ?
    """, (date_from, date_to))
    items_map = {}
    for i in items_all:
        items_map.setdefault(i['order_id'], []).append(i)
    output = io.StringIO()
    writer = csv.writer(output)
    # Sheet 1 header
    writer.writerow(['=== RESTORA — ORDER REPORT ==='])
    writer.writerow([f'Period: {date_from} to {date_to}'])
    writer.writerow([f'Generated: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}'])
    writer.writerow([])
    writer.writerow(['Bill No.', 'Table', 'Waiter', 'Customer', 'Mobile', 'Guests',
                     'Subtotal', 'GST', 'Total', 'Payment Mode', 'Status', 'Order Time', 'Completed Time'])
    total_rev = 0
    for o in orders:
        writer.writerow([
            o['bill_number'] or '—',
            o['table_number'] or '—',
            o['waiter_name'] or '—',
            o['customer_name'] or '—',
            o['customer_mobile'] or '—',
            o['customer_count'] or 1,
            f"₹{float(o['subtotal'] or 0):.2f}",
            f"₹{float(o['gst_amount'] or 0):.2f}",
            f"₹{float(o['total_amount'] or 0):.2f}",
            (o['payment_mode'] or '—').upper(),
            o['status'] or '—',
            o['order_time'] or '—',
            o['completed_time'] or '—'
        ])
        if o['status'] == 'completed':
            total_rev += float(o['total_amount'] or 0)
    
    # Add detailed items section
    writer.writerow([])
    writer.writerow(['=== DETAILED ITEMS ==='])
    writer.writerow(['Bill No.', 'Item Name', 'Quantity (Pcs)', 'Quantity (kg)', 'Unit Price', 'Total Amount'])
    for o in orders:
        oi_list = items_map.get(o['id'], [])
        for item in oi_list:
            qty_disp = f"{item['quantity_kg']:.3f}kg" if item['quantity_kg'] else f"×{item['quantity']}"
            writer.writerow([
                o['bill_number'] or '—',
                item['item_name'],
                item['quantity'] or '—',
                f"{item['quantity_kg']:.3f}" if item['quantity_kg'] else '—',
                f"₹{float(item['price']):.0f}",
                f"₹{float(item['total']):.2f}"
            ])
    writer.writerow([])
    writer.writerow(['', '', '', '', '', '', '', 'TOTAL REVENUE:', f"₹{total_rev:.2f}", '', '', '', ''])
    # writer.writerow([])
    # writer.writerow(['=== ITEM-WISE DETAIL ==='])
    # writer.writerow(['Bill No.', 'Item Name', 'Qty', 'Unit Price', 'Total', 'Special Instruction', 'Status'])
    # for o in orders:
    #     oi_list = items_map.get(o['id'], [])
    #     for item in oi_list:
    #         writer.writerow([
    #             o['bill_number'] or '—',
    #             item['item_name'],
    #             item['quantity'],
    #             f"₹{float(item['price']):.2f}",
    #             f"₹{float(item['total']):.2f}",
    #             item['special_instruction'] or '—',
    #             item['status']
    #         ])
    # output.seek(0)
    fname = f"Restora_Report_{date_from}_to_{date_to}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=fname
    )




@app.route('/api/admin/categories/<int:cid>', methods=['PUT'])
@admin_required
@license_required
def admin_update_category(cid):
    d = request.get_json()
    db_query("UPDATE menu_categories SET name=?, sort_order=?, top_pick=? WHERE id=?",
        (d.get('name'), d.get('sort_order', 0), d.get('top_pick'), cid))
    return jsonify({'ok': True})




@app.route('/api/admin/fix_duplicate_payments', methods=['POST'])
@admin_required
@license_required
def fix_duplicate_payments():
    # Har order ka sirf ek (sabse purana) payment rakho, baaki delete karo
    db_query("""DELETE FROM payments WHERE id NOT IN (
        SELECT MIN(id) FROM payments GROUP BY order_id
    )""")
    return jsonify({'ok': True})

@app.route('/api/admin/fix_unknown_payments', methods=['POST'])
@admin_required
@license_required
def fix_unknown_payments():
    # payments table se orders table mein payment_mode sync karo
    db_query("""UPDATE orders SET payment_mode = (
        SELECT payment_mode FROM payments WHERE payments.order_id = orders.id ORDER BY payments.created_at DESC LIMIT 1
    ) WHERE status='completed' AND (payment_mode IS NULL OR payment_mode='')""")
    return jsonify({'ok': True})

@app.route('/api/cashier/payment_history')
@license_required
def cashier_payment_history():
    if session.get('role') != 'cashier':
        return jsonify([])
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    rows = db_query("""SELECT p.*, o.bill_number, o.token_number, s.name as waiter_name 
        FROM payments p 
        LEFT JOIN orders o ON p.order_id=o.id 
        LEFT JOIN staff s ON p.waiter_id=s.id 
        WHERE date(p.created_at) >= ? 
        ORDER BY p.created_at DESC""", (cutoff,))
    return jsonify(rows)



@app.route('/api/kitchen/orders')
@kitchen_required
@license_required
def kitchen_orders():
    # rows = db_query("SELECT o.id, o.created_at, t.table_number, s.name as waiter_name FROM orders o LEFT JOIN tables t ON o.table_id=t.id LEFT JOIN staff s ON o.waiter_id=s.id WHERE o.status='active' ORDER BY o.created_at ASC")
    
    rows = db_query("SELECT o.id, o.created_at, t.table_number, s.name as waiter_name FROM orders o LEFT JOIN tables t ON o.table_id=t.id LEFT JOIN staff s ON o.waiter_id=s.id WHERE o.status IN ('active','bill_pending') ORDER BY o.created_at ASC")
    result = []
    for r in rows:
        items = db_query("SELECT * FROM order_items WHERE order_id=? AND status != 'served'", (r['id'],))
        r['items'] = items
        if items:
            result.append(r)
    return jsonify(result)

@app.route('/api/kitchen/mark_ready', methods=['POST'])
@kitchen_required
@license_required
def kitchen_mark_ready():
    d = request.get_json()
    db_query("UPDATE order_items SET status='ready' WHERE order_id=?", (d['order_id'],))
    return jsonify({'ok': True})

@app.route('/api/kitchen/completed_orders')
@kitchen_required
@license_required
def kitchen_completed():
    today = str(datetime.date.today())
    return jsonify(db_query("SELECT o.id, o.completed_at, t.table_number, o.total_amount FROM orders o LEFT JOIN tables t ON o.table_id=t.id WHERE o.status='completed' AND date(o.completed_at)=? ORDER BY o.completed_at DESC", (today,)))

# # ─── SCHEDULED TASKS ─────────────────────────────────────────────────────────
# def schedule_auto_cleanup():
#     if not SCHEDULER_AVAILABLE:
#         print("⚠️ APScheduler not available, skipping auto-cleanup")
#         return
#     scheduler = BackgroundScheduler()
#     def cleanup_task():
#         try:
#             cutoff = datetime.date.today() - datetime.timedelta(days=90)
#             db_query("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE date(created_at) < ?)", (str(cutoff),))
#             db_query("DELETE FROM orders WHERE date(created_at) < ?", (str(cutoff),))
#             db_query("DELETE FROM customer_data WHERE date(created_at) < ?", (str(cutoff),))
#             print(f"✅ Auto-cleanup executed: {datetime.datetime.now()}")
#         except Exception as e:
#             print(f"❌ Cleanup error: {e}")
#     scheduler.add_job(cleanup_task, 'cron', day=28, hour=2)
#     scheduler.start()




# ─── SCHEDULED TASKS ─────────────────────────────────────────────────────────
def do_cleanup():
    """90 din se purana order/customer data delete karo. Yearly analytics safe rehti hai."""
    try:
        cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
        # Yearly analytics mein save karo pehle (aggregated)
        db_query("""
            INSERT OR IGNORE INTO yearly_analytics (year, month, total_orders, total_revenue, avg_order)
            SELECT 
                CAST(strftime('%Y', created_at) AS INTEGER),
                CAST(strftime('%m', created_at) AS INTEGER),
                COUNT(*), 
                COALESCE(SUM(total_amount),0),
                COALESCE(AVG(total_amount),0)
            FROM orders 
            WHERE status='completed' AND date(created_at) < ?
            GROUP BY strftime('%Y-%m', created_at)
        """, (cutoff,))
        # Ab delete karo
        db_query("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE date(created_at) < ?)", (cutoff,))
        db_query("DELETE FROM customer_data WHERE date(created_at) < ?", (cutoff,))
        db_query("DELETE FROM orders WHERE date(created_at) < ?", (cutoff,))
        db_query("INSERT INTO data_retention_log (data_type, records_deleted) VALUES (?,?)", ('auto_cleanup', 1))
        print(f"✅ Auto-cleanup done: {datetime.datetime.now()}")
    except Exception as e:
        print(f"❌ Cleanup error: {e}")

def schedule_auto_cleanup():
    if not SCHEDULER_AVAILABLE:
        print("⚠️ APScheduler not available, skipping auto-cleanup")
        return
    scheduler = BackgroundScheduler()
    # Har roz raat 2 baje chalta hai — 90 din se purana data ek ek din karta jaata hai
    scheduler.add_job(do_cleanup, 'cron', hour=2, minute=0)
    scheduler.start()
    print("✅ Auto-cleanup scheduler started (daily 2AM)")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if '--hwid' in sys.argv:
        print(f"\n🔑 System HWID: {get_hwid()}\n")
        sys.exit(0)
    init_db()
    schedule_auto_cleanup()
    import webbrowser, threading
    # url = "http://localhost:9090"
    # threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # print(f"\n✅ Restora chal raha hai: {url}")
    # print(f"🔑 System HWID: {get_hwid()}\n")
    # # app.run(debug=False, host='0.0.0.0', port=9090)

    import socket
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return '0.0.0.0'
    
    # local_ip = get_local_ip()
    # url = f"http://{local_ip}:9090"
    # threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # print(f"\n✅ Restora chal raha hai: {url}")
    # print(f"🔑 System HWID: {get_hwid()}\n")
    # app.run(debug=False, host='0.0.0.0', port=9090)


    local_ip = get_local_ip()
    url = f"http://{local_ip}:4005"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n✅ Restora chal raha hai: {url}")
    print(f"🔑 System HWID: {get_hwid()}\n")
    app.run(debug=False, host='0.0.0.0', port=4005, use_reloader=False)













#########  is k baad walkin wala
