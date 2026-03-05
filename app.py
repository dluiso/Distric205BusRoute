# ============================================================
#  School Bus Tracker — D205 School District
#  Flask + SQLAlchemy + APScheduler + Flask-Mail
# ============================================================

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, make_response, send_file, session, g)
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, date, timedelta
from functools import wraps
from sqlalchemy import func
import os, json, csv, io, pytz, re, threading, uuid, time, secrets, html
from collections import defaultdict

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

try:
    from fpdf import FPDF
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


# ── APP SETUP ────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load instance-level config (.env written by the install wizard or admin)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, 'instance', '.env'), override=False)
except ImportError:
    pass
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
_secret = os.environ.get('SECRET_KEY', '')
if not _secret or _secret == 'changeme-set-in-env':
    _secret = secrets.token_hex(32)   # auto-generate for dev; wizard writes a real key
app.config['SECRET_KEY'] = _secret
_db_url = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(BASE_DIR, "bustrack.db")}')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
# Secure session cookies
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE']  = 'Lax'
app.config['SESSION_COOKIE_SECURE']    = os.environ.get('FLASK_ENV') == 'production'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 h

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'static', 'exports'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
mail = Mail(app)
broadcast_jobs  = {}   # job_id -> {total, sent, failed, done, errors}
_login_attempts = defaultdict(list)   # {ip: [epoch_timestamps]}

# ── INSTALLATION LOCK ─────────────────────────────────────────────────────────
INSTANCE_DIR   = os.path.join(BASE_DIR, 'instance')
INSTALLED_FILE = os.path.join(INSTANCE_DIR, '.installed')

def is_installed():
    return os.path.exists(INSTALLED_FILE)

def _mark_installed():
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    with open(INSTALLED_FILE, 'w') as f:
        f.write('installed')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'ico'}
def allowed_file(fn): return '.' in fn and fn.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ── MODELS ───────────────────────────────────────────────────────────────────

class Configuration(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    # Identity
    app_name            = db.Column(db.String(100), default='School Bus Tracker')
    app_subtitle        = db.Column(db.String(200), default='D205 School District')
    logo_path           = db.Column(db.String(255), default='')
    icon_path           = db.Column(db.String(255), default='')
    # Theme
    theme_mode          = db.Column(db.String(10), default='light')
    color_bg            = db.Column(db.String(20), default='#f1f5f9')
    color_nav           = db.Column(db.String(20), default='#1e293b')
    color_card          = db.Column(db.String(20), default='#ffffff')
    color_text          = db.Column(db.String(20), default='#0f172a')
    color_accent        = db.Column(db.String(20), default='#3b82f6')
    color_nav_text      = db.Column(db.String(20), default='#f8fafc')
    # Operational
    timezone            = db.Column(db.String(50), default='America/New_York')
    daily_reset_time    = db.Column(db.String(5), default='05:00')
    commit_delay_min    = db.Column(db.Integer, default=5)
    offline_message     = db.Column(db.Text, default='Bus service is currently offline. Check back during operational hours.')
    show_always         = db.Column(db.Boolean, default=True)
    # Language
    lang_frontend       = db.Column(db.String(10), default='en')
    lang_backend        = db.Column(db.String(10), default='en')
    time_format         = db.Column(db.String(4), default='12h')
    # Email
    mail_provider       = db.Column(db.String(20), default='custom')
    mail_server         = db.Column(db.String(100), default='')
    mail_port           = db.Column(db.Integer, default=587)
    mail_use_tls        = db.Column(db.Boolean, default=True)
    mail_use_ssl        = db.Column(db.Boolean, default=False)
    mail_username       = db.Column(db.String(100), default='')
    mail_password       = db.Column(db.String(200), default='')
    mail_from_email     = db.Column(db.String(100), default='')
    mail_from_name      = db.Column(db.String(100), default='Bus Tracker')


class OperationalSchedule(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    days        = db.Column(db.String(50), default='mon-fri')   # mon-fri, all, weekend, custom
    start_time  = db.Column(db.String(5), nullable=False)       # HH:MM
    end_time    = db.Column(db.String(5), nullable=False)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class Holiday(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    holiday_type    = db.Column(db.String(50), default='school')  # federal, state, school, local
    holiday_date    = db.Column(db.Date, nullable=False)
    is_recurring    = db.Column(db.Boolean, default=False)
    is_active       = db.Column(db.Boolean, default=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class UserGroup(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_admin    = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    users       = db.relationship('User', backref='group', lazy=True)
    permissions = db.relationship('GroupPermission', backref='group', lazy=True, cascade='all, delete-orphan')


class GroupPermission(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    group_id     = db.Column(db.Integer, db.ForeignKey('user_group.id'), nullable=False)
    module_key   = db.Column(db.String(50), nullable=False)
    access_level = db.Column(db.String(10), default='none')  # none | limited | full
    __table_args__ = (db.UniqueConstraint('group_id', 'module_key'),)


MODULES = [
    {'key': 'buses',         'label': 'Buses',          'icon': 'fa-bus'},
    {'key': 'incidents',     'label': 'Status Types',    'icon': 'fa-exclamation-circle'},
    {'key': 'statistics',    'label': 'Statistics',      'icon': 'fa-chart-bar'},
    {'key': 'users',         'label': 'Users',           'icon': 'fa-users'},
    {'key': 'notifications', 'label': 'Notifications',   'icon': 'fa-bell'},
    {'key': 'config',        'label': 'Configuration',   'icon': 'fa-cog'},
]


class User(UserMixin, db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    username            = db.Column(db.String(80), unique=True, nullable=False)
    email               = db.Column(db.String(120), unique=True, nullable=True)
    password_hash       = db.Column(db.String(256), nullable=False)
    first_name          = db.Column(db.String(80))
    last_name           = db.Column(db.String(80))
    phone               = db.Column(db.String(30))
    workplace           = db.Column(db.String(150))
    job_title           = db.Column(db.String(100))
    group_id            = db.Column(db.Integer, db.ForeignKey('user_group.id'))
    use_email_auth      = db.Column(db.Boolean, default=False)
    receive_notifications = db.Column(db.Boolean, default=True)
    avatar_initials     = db.Column(db.String(4))
    active              = db.Column(db.Boolean, default=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    last_login          = db.Column(db.DateTime)

    @property
    def is_active(self): return self.active

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.username

    @property
    def is_admin(self): return bool(self.group and self.group.is_admin)

    def set_password(self, pwd): self.password_hash = generate_password_hash(pwd)
    def check_password(self, pwd): return check_password_hash(self.password_hash, pwd)

    def has_access(self, module_key, level='limited'):
        if self.is_admin: return True
        if not self.group: return False
        perm = GroupPermission.query.filter_by(group_id=self.group_id, module_key=module_key).first()
        if not perm or perm.access_level == 'none': return False
        if level == 'limited': return perm.access_level in ('limited', 'full')
        if level == 'full': return perm.access_level == 'full'
        return False

    def accessible_modules(self):
        if self.is_admin: return MODULES
        if not self.group: return []
        return [m for m in MODULES if self.has_access(m['key'])]


class BusScheduleType(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(50), unique=True, nullable=False)
    time_label   = db.Column(db.String(20))   # e.g. "7:00 AM"
    sort_order   = db.Column(db.Integer, default=0)
    window_start = db.Column(db.String(5))    # HH:MM display window begins
    window_end   = db.Column(db.String(5))    # HH:MM display window ends


class IncidentType(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    color       = db.Column(db.String(20), default='#6b7280')
    icon        = db.Column(db.String(50), default='fa-circle')
    description = db.Column(db.String(255))
    is_default  = db.Column(db.Boolean, default=False)   # On Time = default
    is_system   = db.Column(db.Boolean, default=False)   # Cannot delete
    sort_order  = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class Bus(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    identifier   = db.Column(db.String(20), nullable=False)  # TR, TRS, TT — not unique alone
    name         = db.Column(db.String(150), nullable=False)
    route        = db.Column(db.String(200))
    capacity     = db.Column(db.Integer)
    description  = db.Column(db.Text)
    active       = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    schedule_assignments = db.relationship('BusScheduleAssignment', backref='bus', lazy=True, cascade='all, delete-orphan')
    incident_records     = db.relationship('BusIncidentRecord', backref='bus', lazy=True)
    __table_args__ = (db.UniqueConstraint('identifier', 'name', name='uq_bus_identifier_name'),)

    @property
    def display_name(self): return f"{self.identifier} — {self.name}"


class BusScheduleAssignment(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    bus_id           = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    schedule_type_id = db.Column(db.Integer, db.ForeignKey('bus_schedule_type.id'), nullable=False)
    departure_time   = db.Column(db.String(5))   # HH:MM specific to this bus
    schedule_type    = db.relationship('BusScheduleType')
    __table_args__   = (db.UniqueConstraint('bus_id', 'schedule_type_id'),)


class DelayReason(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    reason     = db.Column(db.String(200), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BusIncidentRecord(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    bus_id            = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    incident_type_id  = db.Column(db.Integer, db.ForeignKey('incident_type.id'), nullable=False)
    schedule_type_id  = db.Column(db.Integer, db.ForeignKey('bus_schedule_type.id'), nullable=True)
    delay_minutes     = db.Column(db.Integer, default=0)
    eta               = db.Column(db.String(5))    # HH:MM estimated arrival
    delay_reason_id   = db.Column(db.Integer, db.ForeignKey('delay_reason.id'), nullable=True)
    delay_reason_text = db.Column(db.String(200))  # free-text if no preset chosen
    notes             = db.Column(db.Text)
    incident_date     = db.Column(db.Date, default=date.today)
    is_pending        = db.Column(db.Boolean, default=True)
    committed_at      = db.Column(db.DateTime)
    created_by_id     = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    incident_type     = db.relationship('IncidentType')
    schedule_type     = db.relationship('BusScheduleType')
    delay_reason      = db.relationship('DelayReason')
    created_by        = db.relationship('User')


class SubscriberGroup(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(200), default='')
    color       = db.Column(db.String(20), default='blue')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    subscribers = db.relationship('NotificationSubscriber', backref='group', lazy=True)


class NotificationSubscriber(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    first_name     = db.Column(db.String(80))
    last_name      = db.Column(db.String(80))
    email          = db.Column(db.String(120))
    phone          = db.Column(db.String(30))
    active         = db.Column(db.Boolean, default=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    group_id       = db.Column(db.Integer, db.ForeignKey('subscriber_group.id'), nullable=True)
    bus_assignments = db.relationship('NotificationBusAssignment', backref='subscriber', lazy=True, cascade='all, delete-orphan')

    @property
    def full_name(self): return f"{self.first_name or ''} {self.last_name or ''}".strip()


class NotificationBusAssignment(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    subscriber_id = db.Column(db.Integer, db.ForeignKey('notification_subscriber.id'), nullable=False)
    bus_id        = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    bus           = db.relationship('Bus')
    __table_args__ = (db.UniqueConstraint('subscriber_id', 'bus_id'),)


@login_manager.user_loader
def load_user(uid): return User.query.get(int(uid))


# ── JINJA2 GLOBALS ───────────────────────────────────────────────────────────

_cfg_cache = {}

def get_config():
    cfg = Configuration.query.first()
    if not cfg:
        cfg = Configuration()
        db.session.add(cfg)
        db.session.commit()
    return cfg

def hex_to_text_class(hex_color):
    """Return 'text-white' or 'text-gray-900' based on luminance"""
    h = hex_color.lstrip('#')
    if len(h) == 6:
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        lum = (0.299*r + 0.587*g + 0.114*b) / 255
        return 'text-white' if lum < 0.5 else 'text-gray-900'
    return 'text-white'

TRANSLATIONS = {
    'en': {
        'bus_legend': 'Bus Legend', 'filters': 'Filters', 'search': 'Search buses, routes, status…',
        'all_status': 'All Status', 'favorites': 'Favorites First', 'on_time': 'On Time',
        'delayed': 'Delayed', 'delay': 'min delay', 'no_incidents': 'No incidents today',
        'favorite': 'Favorite', 'remove_fav': 'Remove favorite',
        'loading': 'Loading…', 'route': 'Route', 'capacity': 'Capacity',
        'schedule': 'Schedule', 'morning': 'Morning', 'midday': 'Midday', 'afternoon': 'Afternoon',
    },
    'es': {
        'bus_legend': 'Leyenda de Buses', 'filters': 'Filtros', 'search': 'Buscar buses, rutas, estado…',
        'all_status': 'Todos los estados', 'favorites': 'Favoritos primero', 'on_time': 'A Tiempo',
        'delayed': 'Retrasado', 'delay': 'min de retraso', 'no_incidents': 'Sin incidencias hoy',
        'favorite': 'Favorito', 'remove_fav': 'Quitar favorito',
        'loading': 'Cargando…', 'route': 'Ruta', 'capacity': 'Capacidad',
        'schedule': 'Horario', 'morning': 'Mañana', 'midday': 'Medio día', 'afternoon': 'Tarde',
    }
}

def t(key):
    try:
        cfg = get_config()
        lang = cfg.lang_frontend
    except Exception:
        lang = 'en'
    return TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)

def t_admin(key):
    try:
        cfg = get_config()
        lang = cfg.lang_backend
    except Exception:
        lang = 'en'
    return TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)

def fmt_time(time_str, fmt='12h'):
    """Convert HH:MM string to 12h (7:30 AM) or 24h (07:30) display format."""
    if not time_str:
        return ''
    try:
        from datetime import datetime as _dt
        t_obj = _dt.strptime(str(time_str)[:5], '%H:%M')
        if fmt == '12h':
            h, m = t_obj.hour, t_obj.minute
            period = 'AM' if h < 12 else 'PM'
            h12 = h % 12 or 12
            return f'{h12}:{m:02d} {period}'
        return f'{t_obj.hour:02d}:{t_obj.minute:02d}'
    except Exception:
        return str(time_str)

def _csrf_token():
    """Generate (or retrieve) per-session CSRF token, stored in Flask session."""
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(32)
    return session['_csrf']

app.jinja_env.globals.update(
    get_config=get_config,
    MODULES=MODULES,
    hex_to_text_class=hex_to_text_class,
    t=t, t_admin=t_admin,
    fmt_time=fmt_time,
    csrf_token=_csrf_token,
)


# ── DB INITIALIZATION ────────────────────────────────────────────────────────

def _migrate_bus_table():
    """Migrate bus table: replace unique(identifier) with unique(identifier, name)."""
    try:
        from sqlalchemy import inspect as sa_inspect, text
        insp = sa_inspect(db.engine)
        if 'bus' not in insp.get_table_names():
            return  # table doesn't exist yet, create_all will handle it
        unique_cols = [
            set(c['column_names'])
            for c in insp.get_unique_constraints('bus')
        ]
        # Check if old constraint (only on identifier) still exists
        if {'identifier'} in unique_cols:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE bus RENAME TO bus_old'))
                conn.commit()
            db.create_all()  # creates bus with new schema
            with db.engine.connect() as conn:
                conn.execute(text('INSERT INTO bus SELECT * FROM bus_old'))
                conn.execute(text('DROP TABLE bus_old'))
                conn.commit()
            print('[Migration] bus table: unique constraint updated to (identifier, name)')
    except Exception as e:
        print(f'[Migration] bus table skipped: {e}')


def _migrate_add_columns():
    """Add new columns to existing tables (safe: ignores if already exists)."""
    from sqlalchemy import text
    cols = [
        ('bus_schedule_assignment', 'departure_time', 'VARCHAR(5)'),
        ('bus_incident_record',     'eta',             'VARCHAR(5)'),
        ('bus_incident_record',     'delay_reason_id', 'INTEGER'),
        ('bus_incident_record',     'delay_reason_text', 'VARCHAR(200)'),
        ('configuration',           'mail_use_ssl',    'BOOLEAN DEFAULT 0'),
        ('configuration',           'time_format',     "VARCHAR(4) DEFAULT '12h'"),
        ('notification_subscriber', 'group_id',        'INTEGER'),
        ('bus_schedule_type',       'window_start',    'VARCHAR(5)'),
        ('bus_schedule_type',       'window_end',      'VARCHAR(5)'),
    ]
    with db.engine.connect() as conn:
        for table, col, coltype in cols:
            try:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {coltype}'))
                conn.commit()
            except Exception:
                pass  # column already exists


def init_db():
    _migrate_bus_table()
    db.create_all()
    _migrate_add_columns()
    _seed_defaults()

    # Auto-detect existing installations: if users exist but no lock file, create it.
    if not is_installed() and User.query.count() > 0:
        _mark_installed()


# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_current_period():
    """Returns the active BusScheduleType based on current local time, or None."""
    try:
        cfg = get_config()
        tz = pytz.timezone(cfg.timezone)
        now = datetime.now(tz)
        current_time = now.strftime('%H:%M')
        periods = BusScheduleType.query.filter(
            BusScheduleType.window_start != None,
            BusScheduleType.window_end   != None,
        ).order_by(BusScheduleType.sort_order).all()
        for p in periods:
            if p.window_start and p.window_end and p.window_start <= current_time <= p.window_end:
                return p
    except Exception:
        pass
    return None


def get_bus_status(bus_id, for_date=None, schedule_type_id=None):
    """Returns (IncidentType, delay_minutes) for a bus on a given date/period."""
    if for_date is None: for_date = date.today()
    q = BusIncidentRecord.query.filter_by(bus_id=bus_id, incident_date=for_date)
    if schedule_type_id:
        q = q.filter_by(schedule_type_id=schedule_type_id)
    rec = q.order_by(BusIncidentRecord.created_at.desc()).first()
    if rec:
        return rec.incident_type, rec.delay_minutes
    default = IncidentType.query.filter_by(is_default=True).first()
    return default, 0

def is_operational():
    """Check current time against operational schedules. Returns (bool, message)."""
    cfg = get_config()
    if cfg.show_always:
        return True, None
    try:
        tz = pytz.timezone(cfg.timezone)
        now = datetime.now(tz)
        today_str = now.strftime('%A').lower()[:3]  # mon, tue…
        current_time = now.strftime('%H:%M')
        # Check holidays
        holiday = Holiday.query.filter_by(holiday_date=now.date(), is_active=True).first()
        if holiday:
            return False, f"Service unavailable — {holiday.name}"
        # Check schedules
        schedules = OperationalSchedule.query.filter_by(is_active=True).all()
        for s in schedules:
            days = s.days
            applies = (
                days == 'all' or
                (days == 'mon-fri' and today_str in ('mon','tue','wed','thu','fri')) or
                (days == 'weekend' and today_str in ('sat','sun')) or
                today_str in days
            )
            if applies and s.start_time <= current_time <= s.end_time:
                return True, None
        return False, cfg.offline_message
    except Exception:
        return True, None

def bus_list_today(period=None):
    """Return bus status list for today, optionally filtered to a specific period.

    When ``period`` is a BusScheduleType, only buses assigned to that period are
    returned and their status is resolved against period-specific incidents only.
    Pass period=None to bypass filtering (admin views, statistics, etc.).
    """
    today = date.today()
    current_period = period  # caller may override; default = auto-detect
    if current_period is None:
        current_period = get_current_period()

    if current_period is not None:
        assigned_ids = {a.bus_id for a in BusScheduleAssignment.query.filter_by(
            schedule_type_id=current_period.id).all()}
        buses = Bus.query.filter(
            Bus.active == True,
            Bus.id.in_(assigned_ids),
        ).order_by(Bus.identifier).all()
    else:
        buses = Bus.query.filter_by(active=True).order_by(Bus.identifier).all()

    period_id = current_period.id if current_period else None
    result = []
    for bus in buses:
        status, delay = get_bus_status(bus.id, today, schedule_type_id=period_id)
        q = BusIncidentRecord.query.filter_by(bus_id=bus.id, incident_date=today)
        if period_id:
            q = q.filter_by(schedule_type_id=period_id)
        incidents = q.order_by(BusIncidentRecord.created_at.desc()).all()
        schedules = [a.schedule_type for a in bus.schedule_assignments]
        latest = incidents[0] if incidents else None
        eta = latest.eta if latest else None
        if latest and latest.delay_reason_id and latest.delay_reason:
            delay_reason = latest.delay_reason.reason
        elif latest and latest.delay_reason_text:
            delay_reason = latest.delay_reason_text
        else:
            delay_reason = None
        result.append({'bus': bus, 'status': status, 'delay': delay,
                       'incidents': incidents, 'schedules': schedules,
                       'schedule_assignments': bus.schedule_assignments,
                       'eta': eta, 'delay_reason': delay_reason,
                       'current_period': current_period})
    return result

def configure_mail(cfg, override=None):
    """Apply SMTP settings to Flask-Mail. Pass override dict to use custom values without DB save.

    Flask-Mail caches its state in app.extensions['mail'] at startup, so we must call
    mail.init_app(app) after every app.config.update() to force it to reload.
    """
    # Presets supply the server hostname; port/security can be overridden by the user
    PRESET_SERVERS = {
        'office365': 'smtp.office365.com',
        'google':    'smtp.gmail.com',
    }
    o = override or {}
    provider = o.get('provider', cfg.mail_provider)

    if override:
        # Live test: use form values; derive server from preset if applicable
        srv  = PRESET_SERVERS.get(provider) or o.get('server', cfg.mail_server) or ''
        port = int(o.get('port', None) or cfg.mail_port or 587)
        tls  = bool(o.get('use_tls', cfg.mail_use_tls))
        ssl  = bool(o.get('use_ssl', getattr(cfg, 'mail_use_ssl', False)))
        username   = o.get('username',   cfg.mail_username) or ''
        password   = o.get('password',   '') or cfg.mail_password or ''
        from_email = o.get('from_email', cfg.mail_from_email) or ''
        from_name  = o.get('from_name',  cfg.mail_from_name)  or 'Bus Tracker'
    else:
        # Normal send: use DB values; derive server from preset if applicable
        srv  = PRESET_SERVERS.get(cfg.mail_provider) or cfg.mail_server or ''
        port = cfg.mail_port or 587
        tls  = bool(cfg.mail_use_tls)
        ssl  = bool(getattr(cfg, 'mail_use_ssl', False))
        username   = cfg.mail_username   or ''
        password   = cfg.mail_password   or ''
        from_email = cfg.mail_from_email or ''
        from_name  = cfg.mail_from_name  or 'Bus Tracker'

    app.config.update(
        MAIL_SERVER=srv,
        MAIL_PORT=port,
        MAIL_USE_TLS=tls,
        MAIL_USE_SSL=ssl,
        MAIL_USERNAME=username,
        MAIL_PASSWORD=password,
        MAIL_DEFAULT_SENDER=(from_name, from_email),
        MAIL_SUPPRESS_SEND=False,
    )
    # CRITICAL: Flask-Mail caches its _Mail state object at init time in
    # app.extensions['mail']. Without re-calling init_app(), mail.send() would
    # still use the old cached server/port and connect to localhost instead.
    mail.init_app(app)


# ── PERMISSION DECORATOR ─────────────────────────────────────────────────────

def require_module(module_key, level='limited'):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.url))
            if not current_user.has_access(module_key, level):
                flash('You do not have permission to access this section.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator


# ── APSCHEDULER ──────────────────────────────────────────────────────────────

def commit_pending_incidents():
    with app.app_context():
        try:
            cfg = Configuration.query.first()
            delay = cfg.commit_delay_min if cfg else 5
            cutoff = datetime.utcnow() - timedelta(minutes=delay)
            pending = BusIncidentRecord.query.filter(
                BusIncidentRecord.is_pending == True,
                BusIncidentRecord.created_at <= cutoff
            ).all()
            for rec in pending:
                rec.is_pending = False
                rec.committed_at = datetime.utcnow()
                _send_bus_notifications(rec)
            if pending:
                db.session.commit()
        except Exception as e:
            print(f'[Scheduler] commit error: {e}')

def _send_bus_notifications(rec):
    try:
        cfg = Configuration.query.first()
        if not cfg or (not cfg.mail_server and cfg.mail_provider == 'custom'):
            return
        configure_mail(cfg)
        bus = rec.bus
        it  = rec.incident_type
        subject = f"Bus Update: {bus.display_name}"
        body = (f"Bus {bus.display_name} — Status Update\n\n"
                f"Status: {it.name}\n"
                f"Delay: {rec.delay_minutes} minutes\n"
                f"Notes: {rec.notes or 'N/A'}\n\n"
                f"Sent by {cfg.app_name}")

        sent_emails = set()

        # Send only to subscribers explicitly assigned to this bus
        assignments = NotificationBusAssignment.query.filter_by(bus_id=rec.bus_id).all()
        for a in assignments:
            sub = a.subscriber
            if not sub.active or not sub.email:
                continue
            if sub.email in sent_emails:
                continue
            sent_emails.add(sub.email)
            try:
                msg = Message(subject=subject, recipients=[sub.email], body=body)
                mail.send(msg)
            except Exception as e:
                print(f'[Notifications] subscriber send error: {e}')

    except Exception as e:
        print(f'[Notifications] send error: {e}')

if SCHEDULER_AVAILABLE:
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(commit_pending_incidents, 'interval', minutes=1, id='commit_pending')
    scheduler.start()


# ── SECURITY MIDDLEWARE ───────────────────────────────────────────────────────

_WIZARD_ENDPOINTS = {'install_wizard', 'install_test_db', 'install_run', 'static', 'health'}
_PUBLIC_ENDPOINTS = {'index', 'api_buses'}

@app.before_request
def pre_request_checks():
    ep = request.endpoint
    if ep is None:
        return  # 404 — handled by error handler

    # 1. Redirect to wizard if not yet installed
    if ep not in _WIZARD_ENDPOINTS:
        if not is_installed():
            return redirect(url_for('install_wizard'))

    # 2. CSRF validation on all admin state-changing requests
    if request.path.startswith('/admin/') and request.method == 'POST':
        token  = request.form.get('_csrf') or request.headers.get('X-CSRF-Token', '')
        stored = session.get('_csrf', '')
        if not (token and stored and secrets.compare_digest(str(token), str(stored))):
            abort(403)


@app.after_request
def security_headers(resp):
    resp.headers['X-Content-Type-Options']  = 'nosniff'
    resp.headers['X-Frame-Options']         = 'SAMEORIGIN'
    resp.headers['X-XSS-Protection']        = '1; mode=block'
    resp.headers['Referrer-Policy']         = 'strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy']      = 'geolocation=(), microphone=(), camera=()'
    return resp


# ── ERROR HANDLERS ────────────────────────────────────────────────────────────

@app.errorhandler(403)
def err_403(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def err_404(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def err_500(e):
    return render_template('errors/500.html'), 500


# ── HEALTH CHECK ──────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return 'ok', 200


# ── INSTALL WIZARD ────────────────────────────────────────────────────────────

@app.route('/install')
def install_wizard():
    if is_installed():
        return redirect(url_for('login'))
    return render_template('install/wizard.html')


@app.route('/install/test-db', methods=['POST'])
def install_test_db():
    """AJAX: test a DB connection using sqlalchemy without changing the running app."""
    from sqlalchemy import create_engine, text as sa_text
    data   = request.get_json(silent=True) or {}
    db_url = _build_db_url(data)
    if not db_url:
        return jsonify({'ok': False, 'message': 'Invalid database configuration.'})
    try:
        engine = create_engine(db_url, connect_args={'connect_timeout': 5} if 'postgresql' in db_url else {})
        with engine.connect() as conn:
            conn.execute(sa_text('SELECT 1'))
        return jsonify({'ok': True, 'message': 'Connection successful.'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})


@app.route('/install/run', methods=['POST'])
def install_run():
    if is_installed():
        return jsonify({'ok': False, 'message': 'Already installed.'})

    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    email    = data.get('email', '').strip() or None

    # Validate admin credentials
    if not username or len(username) < 3:
        return jsonify({'ok': False, 'message': 'Username must be at least 3 characters.'})
    if not password or len(password) < 8:
        return jsonify({'ok': False, 'message': 'Password must be at least 8 characters.'})

    try:
        # Write instance/.env (DB URL + SECRET_KEY) if provided
        db_data = data.get('db', {})
        new_db_url = _build_db_url(db_data) if db_data.get('type') else None
        secret_key = secrets.token_hex(32)
        _write_instance_env(secret_key, new_db_url)

        # Re-apply SECRET_KEY immediately
        app.config['SECRET_KEY'] = secret_key

        # If DB changed, reinitialize engine
        if new_db_url and new_db_url != app.config['SQLALCHEMY_DATABASE_URI']:
            app.config['SQLALCHEMY_DATABASE_URI'] = new_db_url
            with app.app_context():
                db.engine.dispose()

        # Create all tables and default data
        db.create_all()
        _migrate_add_columns()
        _seed_defaults()

        # Create admin user
        ag = UserGroup.query.filter_by(is_admin=True).first()
        if not ag:
            ag = UserGroup(name='Administrator', description='Full system access', is_admin=True)
            db.session.add(ag); db.session.commit()
        u = User(username=username, email=email,
                 first_name='Admin', group_id=ag.id, active=True)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()

        _mark_installed()
        return jsonify({'ok': True, 'message': 'Installation complete. Redirecting to login…'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'message': f'Installation failed: {str(e)}'})


def _build_db_url(d):
    """Build a SQLAlchemy DB URL from wizard form data dict."""
    db_type = d.get('type', 'sqlite')
    if db_type == 'sqlite':
        path = d.get('path') or os.path.join(BASE_DIR, 'bustrack.db')
        return f'sqlite:///{path}'
    if db_type == 'postgresql':
        host = d.get('host', 'localhost')
        port = int(d.get('port') or 5432)
        name = d.get('name', 'bustrack')
        user = d.get('user', '')
        pwd  = d.get('password', '')
        return f'postgresql://{user}:{pwd}@{host}:{port}/{name}'
    return None


def _write_instance_env(secret_key, db_url=None):
    """Persist SECRET_KEY (and optionally DATABASE_URL) to instance/.env."""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    env_path = os.path.join(INSTANCE_DIR, '.env')
    lines = [f'SECRET_KEY={secret_key}\n']
    if db_url:
        lines.append(f'DATABASE_URL={db_url}\n')
    with open(env_path, 'w') as f:
        f.writelines(lines)


def _seed_defaults():
    """Insert default groups, schedule types, incident types, delay reasons, schedule and config."""
    # Groups
    if not UserGroup.query.filter_by(name='Administrator').first():
        db.session.add(UserGroup(name='Administrator', description='Full system access', is_admin=True))
        db.session.commit()
    if not UserGroup.query.filter_by(name='Staff Member').first():
        sg = UserGroup(name='Staff Member', description='Limited operational access', is_admin=False)
        db.session.add(sg); db.session.commit()
        sg = UserGroup.query.filter_by(name='Staff Member').first()
        for mod in ['buses', 'incidents', 'statistics']:
            db.session.add(GroupPermission(group_id=sg.id, module_key=mod, access_level='full'))
        for mod in ['users', 'notifications', 'config']:
            db.session.add(GroupPermission(group_id=sg.id, module_key=mod, access_level='none'))
        db.session.commit()
    # Config singleton
    if not Configuration.query.first():
        db.session.add(Configuration()); db.session.commit()
    # Schedule types (with default time windows)
    for name, label, order, w_start, w_end in [
        ('Morning',   '7:00 AM',  0, '06:00', '11:30'),
        ('Midday',    '12:00 PM', 1, '11:30', '14:00'),
        ('Afternoon', '3:00 PM',  2, '14:00', '19:00'),
    ]:
        existing = BusScheduleType.query.filter_by(name=name).first()
        if not existing:
            db.session.add(BusScheduleType(name=name, time_label=label, sort_order=order,
                                           window_start=w_start, window_end=w_end))
        elif not existing.window_start:
            existing.window_start = w_start
            existing.window_end   = w_end
    db.session.commit()
    # Incident types
    for name, color, icon, is_def, is_sys, order in [
        ('On Time','#10b981','fa-check-circle',True,True,0),
        ('Delayed','#f59e0b','fa-clock',False,True,1),
        ('E-Learning','#8b5cf6','fa-laptop',False,True,2),
        ('Combined','#3b82f6','fa-link',False,True,3),
        ('Double-back','#06b6d4','fa-redo',False,True,4),
        ('Out of Service','#ef4444','fa-ban',False,True,5),
        ('Combined/Delayed','#f97316','fa-exclamation-triangle',False,True,6),
    ]:
        if not IncidentType.query.filter_by(name=name).first():
            db.session.add(IncidentType(name=name, color=color, icon=icon,
                                        is_default=is_def, is_system=is_sys, sort_order=order))
    db.session.commit()
    # Delay reasons
    for reason, order in [('Traffic congestion',0),('Road construction',1),('Weather conditions',2),
                           ('Mechanical issue',3),('Driver delay',4),('Student boarding delay',5),
                           ('Accident on route',6),('Detour required',7)]:
        if not DelayReason.query.filter_by(reason=reason).first():
            db.session.add(DelayReason(reason=reason, sort_order=order))
    db.session.commit()
    # Operational schedule
    if not OperationalSchedule.query.first():
        db.session.add(OperationalSchedule(name='Weekday Service', days='mon-fri',
                                           start_time='06:30', end_time='18:00', is_active=True))
        db.session.commit()


# ── PUBLIC ROUTES ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    cfg = get_config()
    operational, offline_msg = is_operational()
    current_period = get_current_period() if operational else None
    buses_data     = bus_list_today(period=current_period) if operational else []
    incident_types = IncidentType.query.order_by(IncidentType.sort_order).all()
    schedule_types = BusScheduleType.query.order_by(BusScheduleType.sort_order).all()
    return render_template('public/index.html',
                           buses_data=buses_data, incident_types=incident_types,
                           schedule_types=schedule_types, cfg=cfg,
                           current_period=current_period,
                           operational=operational, offline_msg=offline_msg,
                           today=date.today())

@app.route('/api/buses')
def api_buses():
    operational, _ = is_operational()
    if not operational:
        return jsonify({'operational': False, 'buses': []})
    today          = date.today()
    current_period = get_current_period()
    buses_data     = bus_list_today(period=current_period)
    result = []
    for item in buses_data:
        bus = item['bus']
        status = item['status']
        result.append({
            'id': bus.id, 'identifier': bus.identifier, 'name': bus.name,
            'display_name': bus.display_name, 'route': bus.route or '',
            'capacity': bus.capacity, 'description': bus.description or '',
            'status': {'name': status.name if status else 'On Time',
                       'color': status.color if status else '#10b981',
                       'icon': status.icon if status else 'fa-check-circle',
                       'is_default': status.is_default if status else True},
            'delay_minutes': item['delay'],
            'incidents': [{'type': i.incident_type.name, 'color': i.incident_type.color,
                           'icon': i.incident_type.icon, 'delay': i.delay_minutes,
                           'notes': i.notes or '', 'time': i.created_at.strftime('%H:%M'),
                           'schedule': i.schedule_type.name if i.schedule_type else ''}
                          for i in item['incidents']],
            'schedules': [s.name for s in item['schedules']],
        })
    period_info = {'id': current_period.id, 'name': current_period.name} if current_period else None
    return jsonify({'operational': True, 'current_period': period_info, 'buses': result})


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        ip  = request.remote_addr or '0.0.0.0'
        now = time.time()
        # Purge attempts older than 5 minutes, then enforce limit
        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < 300]
        if len(_login_attempts[ip]) >= 5:
            flash('Too many failed attempts. Please wait 5 minutes and try again.', 'error')
            return render_template('admin/login.html')

        identifier = request.form.get('username', '').strip()
        password   = request.form.get('password', '')
        user = User.query.filter_by(username=identifier).first()
        if not user:
            user = User.query.filter_by(email=identifier, use_email_auth=True).first()
        if user and user.check_password(password) and user.active:
            _login_attempts[ip].clear()
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            # Prevent open-redirect: only allow relative next URLs
            next_url = request.args.get('next', '')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect(url_for('dashboard'))
        _login_attempts[ip].append(now)
        flash('Invalid credentials. Please try again.', 'error')
    return render_template('admin/login.html')

@app.route('/admin/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@app.route('/admin/')
@app.route('/admin/dashboard')
@login_required
def dashboard():
    today = date.today()
    # Date filter
    period = request.args.get('period', 'today')
    date_from = request.args.get('date_from', today.isoformat())
    date_to   = request.args.get('date_to',   today.isoformat())

    if period == 'today':
        d_from = d_to = today
    elif period == 'week':
        d_from = today - timedelta(days=today.weekday())
        d_to   = today
    elif period == 'month':
        d_from = today.replace(day=1)
        d_to   = today
    elif period == 'year':
        d_from = today.replace(month=1, day=1)
        d_to   = today
    else:
        try:
            d_from = date.fromisoformat(date_from)
            d_to   = date.fromisoformat(date_to)
        except Exception:
            d_from = d_to = today

    buses      = Bus.query.filter_by(active=True).all()
    total_buses = len(buses)

    # Today's status summary
    on_time_count = 0
    for bus in buses:
        status, _ = get_bus_status(bus.id, today)
        if status and status.is_default:
            on_time_count += 1

    # All incidents in period (pending + committed)
    period_incidents = BusIncidentRecord.query.filter(
        BusIncidentRecord.incident_date >= d_from,
        BusIncidentRecord.incident_date <= d_to,
    ).all()
    pending_count = sum(1 for inc in period_incidents if inc.is_pending)

    # Chart data: by incident type (only non-default types)
    by_type = {}
    by_type_colors = {}
    for inc in period_incidents:
        if not inc.incident_type.is_default:
            name = inc.incident_type.name
            by_type[name] = by_type.get(name, 0) + 1
            by_type_colors[name] = inc.incident_type.color

    # Chart data: by bus (only buses with non-default incidents)
    by_bus = {}
    for inc in period_incidents:
        if not inc.incident_type.is_default:
            n = inc.bus.identifier
            by_bus[n] = by_bus.get(n, 0) + 1

    # Chart data: incidents per day (non-default only)
    by_day = {}
    for inc in period_incidents:
        if not inc.incident_type.is_default:
            d = inc.incident_date.isoformat()
            by_day[d] = by_day.get(d, 0) + 1

    recent = BusIncidentRecord.query.filter(
        BusIncidentRecord.incident_date >= d_from,
    ).order_by(BusIncidentRecord.created_at.desc()).limit(15).all()

    buses_today     = bus_list_today()
    incident_types  = IncidentType.query.order_by(IncidentType.sort_order).all()
    all_buses       = Bus.query.filter_by(active=True).order_by(Bus.identifier).all()

    # period_incidents count: only non-default (actual incidents)
    actual_incidents = sum(1 for inc in period_incidents if not inc.incident_type.is_default)

    return render_template('admin/dashboard.html',
        total_buses=total_buses, on_time_count=on_time_count,
        with_incidents=total_buses - on_time_count,
        period_incidents=actual_incidents,
        pending_count=pending_count,
        buses_today=buses_today,
        recent=recent, period=period,
        date_from=d_from.isoformat(), date_to=d_to.isoformat(),
        by_type_json=json.dumps(by_type),
        by_type_colors_json=json.dumps(list(by_type_colors.values())),
        by_bus_json=json.dumps(by_bus),
        by_day_json=json.dumps(by_day),
        incident_types=incident_types, all_buses=all_buses,
        today=today,
    )


# ── BUSES MODULE ──────────────────────────────────────────────────────────────

@app.route('/admin/buses')
@login_required
@require_module('buses')
def buses():
    today          = date.today()
    current_period = get_current_period()
    buses_data     = bus_list_today(period=current_period)
    incident_types = IncidentType.query.order_by(IncidentType.sort_order).all()
    schedule_types = BusScheduleType.query.order_by(BusScheduleType.sort_order).all()
    delay_reasons  = DelayReason.query.order_by(DelayReason.sort_order).all()
    can_write      = current_user.has_access('buses', 'full')
    return render_template('admin/buses.html',
                           buses_data=buses_data, incident_types=incident_types,
                           schedule_types=schedule_types, delay_reasons=delay_reasons,
                           current_period=current_period,
                           can_write=can_write, today=today)

@app.route('/admin/buses/add', methods=['POST'])
@login_required
@require_module('buses', 'full')
def add_bus():
    identifier = request.form.get('identifier', '').strip().upper()
    name       = request.form.get('name', '').strip()
    if not identifier or not name:
        flash('Identifier and name are required.', 'error')
        return redirect(url_for('buses'))
    if Bus.query.filter_by(identifier=identifier, name=name).first():
        flash(f'A bus with identifier "{identifier}" and name "{name}" already exists.', 'error')
        return redirect(url_for('buses'))
    bus = Bus(identifier=identifier, name=name,
              route=request.form.get('route','').strip() or None,
              capacity=request.form.get('capacity', type=int),
              description=request.form.get('description','').strip() or None)
    db.session.add(bus)
    db.session.flush()
    for sid in request.form.getlist('schedule_ids'):
        dep_time = request.form.get(f'departure_time_{sid}', '').strip() or None
        db.session.add(BusScheduleAssignment(bus_id=bus.id, schedule_type_id=int(sid),
                                             departure_time=dep_time))
    db.session.commit()
    flash(f'Bus {bus.display_name} registered successfully.', 'success')
    return redirect(url_for('buses'))

@app.route('/admin/buses/<int:bus_id>/edit', methods=['POST'])
@login_required
@require_module('buses', 'full')
def edit_bus(bus_id):
    bus = Bus.query.get_or_404(bus_id)
    new_identifier = request.form.get('identifier', bus.identifier).strip().upper()
    new_name       = request.form.get('name', bus.name).strip()
    # Check duplicate only if identifier+name changed
    if (new_identifier != bus.identifier or new_name != bus.name):
        dup = Bus.query.filter_by(identifier=new_identifier, name=new_name).first()
        if dup and dup.id != bus_id:
            flash(f'A bus with identifier "{new_identifier}" and name "{new_name}" already exists.', 'error')
            return redirect(url_for('buses'))
    bus.identifier  = new_identifier
    bus.name        = new_name
    bus.route       = request.form.get('route', '').strip() or None
    bus.capacity    = request.form.get('capacity', type=int)
    bus.description = request.form.get('description', '').strip() or None
    bus.active      = 'active' in request.form
    # Update schedules
    BusScheduleAssignment.query.filter_by(bus_id=bus_id).delete()
    for sid in request.form.getlist('schedule_ids'):
        dep_time = request.form.get(f'departure_time_{sid}', '').strip() or None
        db.session.add(BusScheduleAssignment(bus_id=bus_id, schedule_type_id=int(sid),
                                             departure_time=dep_time))
    db.session.commit()
    flash(f'Bus {bus.display_name} updated.', 'success')
    return redirect(url_for('buses'))

@app.route('/admin/buses/<int:bus_id>/delete', methods=['POST'])
@login_required
@require_module('buses', 'full')
def delete_bus(bus_id):
    bus = Bus.query.get_or_404(bus_id)
    bus.active = False
    db.session.commit()
    flash(f'Bus {bus.identifier} deactivated.', 'success')
    return redirect(url_for('buses'))

@app.route('/admin/buses/<int:bus_id>/incident', methods=['POST'])
@login_required
@require_module('buses', 'full')
def add_bus_incident(bus_id):
    bus = Bus.query.get_or_404(bus_id)
    inc_type_id = request.form.get('incident_type_id', type=int)
    if not inc_type_id:
        flash('Select an incident type.', 'error')
        return redirect(url_for('buses'))
    # Handle delay reason: preset or free-text
    reason_id   = request.form.get('delay_reason_id', type=int) or None
    reason_text = request.form.get('delay_reason_text', '').strip() or None
    if reason_id:
        reason_text = None  # preset takes precedence
    rec = BusIncidentRecord(
        bus_id=bus_id, incident_type_id=inc_type_id,
        schedule_type_id=request.form.get('schedule_type_id', type=int) or None,
        delay_minutes=request.form.get('delay_minutes', 0, type=int),
        eta=request.form.get('eta', '').strip() or None,
        delay_reason_id=reason_id,
        delay_reason_text=reason_text,
        notes=request.form.get('notes', '').strip() or None,
        incident_date=date.today(), is_pending=True,
        created_by_id=current_user.id,
    )
    db.session.add(rec)
    db.session.commit()
    flash(f'Incident recorded for {bus.identifier}.', 'success')
    return redirect(url_for('buses'))

@app.route('/admin/bus-incidents/<int:rec_id>/delete', methods=['POST'])
@login_required
@require_module('buses', 'full')
def delete_bus_incident(rec_id):
    rec = BusIncidentRecord.query.get_or_404(rec_id)
    db.session.delete(rec)
    db.session.commit()
    flash('Incident removed.', 'success')
    return redirect(request.referrer or url_for('buses'))

@app.route('/admin/delay-reasons/add', methods=['POST'])
@login_required
@require_module('buses', 'full')
def add_delay_reason():
    reason = request.form.get('reason', '').strip()
    if not reason:
        return jsonify({'success': False, 'error': 'Reason text required'})
    existing = DelayReason.query.filter_by(reason=reason).first()
    if existing:
        return jsonify({'success': True, 'id': existing.id, 'reason': existing.reason})
    dr = DelayReason(reason=reason, sort_order=99)
    db.session.add(dr)
    db.session.commit()
    return jsonify({'success': True, 'id': dr.id, 'reason': dr.reason})


# ── INCIDENT TYPES MODULE ─────────────────────────────────────────────────────

@app.route('/admin/incidents')
@login_required
@require_module('incidents')
def incidents():
    types     = IncidentType.query.order_by(IncidentType.sort_order, IncidentType.name).all()
    can_write = current_user.has_access('incidents', 'full')
    return render_template('admin/incidents.html', incident_types=types, can_write=can_write)

@app.route('/admin/incidents/add', methods=['POST'])
@login_required
@require_module('incidents', 'full')
def add_incident_type():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Name is required.', 'error')
        return redirect(url_for('incidents'))
    if IncidentType.query.filter_by(name=name).first():
        flash(f'"{name}" already exists.', 'error')
        return redirect(url_for('incidents'))
    it = IncidentType(name=name, color=request.form.get('color', '#6b7280'),
                      icon=request.form.get('icon', 'fa-circle'),
                      description=request.form.get('description', '').strip() or None)
    db.session.add(it)
    db.session.commit()
    flash(f'Status type "{name}" created.', 'success')
    return redirect(url_for('incidents'))

@app.route('/admin/incidents/<int:type_id>/edit', methods=['POST'])
@login_required
@require_module('incidents', 'full')
def edit_incident_type(type_id):
    it = IncidentType.query.get_or_404(type_id)
    it.name        = request.form.get('name', it.name).strip()
    it.color       = request.form.get('color', it.color)
    it.icon        = request.form.get('icon', it.icon)
    it.description = request.form.get('description', '').strip() or None
    db.session.commit()
    flash('Status type updated.', 'success')
    return redirect(url_for('incidents'))

@app.route('/admin/incidents/<int:type_id>/delete', methods=['POST'])
@login_required
@require_module('incidents', 'full')
def delete_incident_type(type_id):
    it = IncidentType.query.get_or_404(type_id)
    if it.is_system:
        flash('Cannot delete a system status type.', 'error')
        return redirect(url_for('incidents'))
    if BusIncidentRecord.query.filter_by(incident_type_id=type_id).first():
        flash('Cannot delete: this type has incident records.', 'error')
        return redirect(url_for('incidents'))
    db.session.delete(it)
    db.session.commit()
    flash(f'"{it.name}" deleted.', 'success')
    return redirect(url_for('incidents'))


# ── STATISTICS MODULE ─────────────────────────────────────────────────────────

@app.route('/admin/statistics')
@login_required
@require_module('statistics')
def statistics():
    today    = date.today()
    period   = request.args.get('period', 'today')
    d_from_s = request.args.get('date_from', today.isoformat())
    d_to_s   = request.args.get('date_to',   today.isoformat())
    bus_id   = request.args.get('bus_id', type=int)
    type_id  = request.args.get('type_id', type=int)

    d_from, d_to = _parse_period(period, d_from_s, d_to_s, today)

    q = BusIncidentRecord.query.filter(
        BusIncidentRecord.is_pending == False,
        BusIncidentRecord.incident_date >= d_from,
        BusIncidentRecord.incident_date <= d_to,
    )
    if bus_id:  q = q.filter_by(bus_id=bus_id)
    if type_id: q = q.filter_by(incident_type_id=type_id)
    records = q.order_by(BusIncidentRecord.incident_date.desc(),
                         BusIncidentRecord.created_at.desc()).all()

    by_type   = {}; by_type_colors = {}
    by_bus    = {}; by_day = {}; avg_delay = {}
    for r in records:
        n = r.incident_type.name
        by_type[n]        = by_type.get(n, 0) + 1
        by_type_colors[n] = r.incident_type.color
        b = r.bus.identifier
        by_bus[b] = by_bus.get(b, 0) + 1
        d = r.incident_date.isoformat()
        by_day[d] = by_day.get(d, 0) + 1
        if r.delay_minutes:
            avg_delay[b] = avg_delay.get(b, [])
            avg_delay[b].append(r.delay_minutes)
    avg_delay_final = {k: round(sum(v)/len(v), 1) for k, v in avg_delay.items()}

    # Period × Bus breakdown for the period chart
    schedule_periods = BusScheduleType.query.order_by(BusScheduleType.sort_order).all()
    period_bus_data  = {p.name: {} for p in schedule_periods}
    for r in records:
        pname = r.schedule_type.name if r.schedule_type else None
        if pname and pname in period_bus_data:
            b = r.bus.identifier
            period_bus_data[pname][b] = period_bus_data[pname].get(b, 0) + 1
    # Only keep periods that actually have data
    period_bus_data = {k: v for k, v in period_bus_data.items() if v}
    record_buses = sorted({r.bus.identifier for r in records})

    # ── Bus Audit ─────────────────────────────────────────────────────────
    default_type = IncidentType.query.filter_by(is_default=True).first()
    audit_buses_q = Bus.query.filter_by(active=True).order_by(Bus.identifier)
    if bus_id:
        audit_buses_q = audit_buses_q.filter_by(id=bus_id)
    audit_buses_list = audit_buses_q.all()

    total_days_in_range = (d_to - d_from).days + 1

    # Days per bus that had at least one non-default incident
    bus_incident_dates = {}
    for r in records:
        if not r.incident_type.is_default:
            bus_incident_dates.setdefault(r.bus_id, set()).add(r.incident_date)

    on_time_by_bus = {}
    bus_audit      = {}
    for bus in audit_buses_list:
        inc_days = len(bus_incident_dates.get(bus.id, set()))
        ot_days  = max(0, total_days_in_range - inc_days)
        on_time_by_bus[bus.identifier] = ot_days
        bus_delays = [r.delay_minutes for r in records
                      if r.bus_id == bus.id and r.delay_minutes and r.delay_minutes > 0]
        avg_d = round(sum(bus_delays) / len(bus_delays), 1) if bus_delays else 0.0
        tot_d = sum(bus_delays)
        rate  = round(ot_days / total_days_in_range * 100, 1) if total_days_in_range else 100.0
        bus_audit[bus.identifier] = {
            'name': bus.name, 'route': bus.route or '',
            'total_days': total_days_in_range,
            'on_time_days': ot_days, 'incident_days': inc_days,
            'on_time_rate': rate, 'avg_delay': avg_d, 'total_delay': tot_d,
        }

    # Include On Time in the by_type chart (only when not filtered to a specific type)
    on_time_total = sum(on_time_by_bus.values())
    if on_time_total > 0 and default_type and not type_id:
        by_type[default_type.name]        = on_time_total
        by_type_colors[default_type.name] = default_type.color

    # Stacked datasets for audit chart: {status_name: {data:[...], color:hex}}
    audit_bus_order = [b.identifier for b in audit_buses_list]
    audit_datasets  = {}
    if default_type:
        audit_datasets[default_type.name] = {
            'data':  [on_time_by_bus.get(bid, 0) for bid in audit_bus_order],
            'color': default_type.color,
        }
    for r in records:
        if not r.incident_type.is_default:
            n = r.incident_type.name
            if n not in audit_datasets:
                audit_datasets[n] = {
                    'data':  [0] * len(audit_bus_order),
                    'color': r.incident_type.color,
                }
            if r.bus.identifier in audit_bus_order:
                audit_datasets[n]['data'][audit_bus_order.index(r.bus.identifier)] += 1

    all_buses  = Bus.query.filter_by(active=True).order_by(Bus.identifier).all()
    all_types  = IncidentType.query.order_by(IncidentType.sort_order).all()
    can_export = current_user.has_access('statistics', 'limited')

    return render_template('admin/statistics.html',
        records=records, period=period,
        date_from=d_from.isoformat(), date_to=d_to.isoformat(),
        bus_id=bus_id, type_id=type_id,
        by_type_json=json.dumps(by_type),
        by_type_colors_json=json.dumps(list(by_type_colors.values())),
        by_bus_json=json.dumps(by_bus),
        by_day_json=json.dumps(by_day),
        avg_delay_json=json.dumps(avg_delay_final),
        period_bus_json=json.dumps(period_bus_data),
        record_buses_json=json.dumps(record_buses),
        bus_audit_json=json.dumps(bus_audit),
        audit_datasets_json=json.dumps(audit_datasets),
        audit_bus_order_json=json.dumps(audit_bus_order),
        default_type_name=(default_type.name if default_type else 'On Time'),
        total_days_in_range=total_days_in_range,
        total=len(records), all_buses=all_buses, all_types=all_types,
        can_export=can_export, today=today,
    )

def _parse_period(period, d_from_s, d_to_s, today):
    if period == 'today':    return today, today
    if period == 'week':     return today - timedelta(days=today.weekday()), today
    if period == 'month':    return today.replace(day=1), today
    if period == 'year':     return today.replace(month=1, day=1), today
    try:
        return date.fromisoformat(d_from_s), date.fromisoformat(d_to_s)
    except Exception:
        return today, today

@app.route('/admin/statistics/export/<fmt>')
@login_required
@require_module('statistics')
def export_statistics(fmt):
    today    = date.today()
    period   = request.args.get('period', 'today')
    d_from_s = request.args.get('date_from', today.isoformat())
    d_to_s   = request.args.get('date_to',   today.isoformat())
    bus_id   = request.args.get('bus_id', type=int)
    type_id  = request.args.get('type_id', type=int)
    d_from, d_to = _parse_period(period, d_from_s, d_to_s, today)

    q = BusIncidentRecord.query.filter(
        BusIncidentRecord.is_pending == False,
        BusIncidentRecord.incident_date >= d_from,
        BusIncidentRecord.incident_date <= d_to,
    )
    if bus_id:  q = q.filter_by(bus_id=bus_id)
    if type_id: q = q.filter_by(incident_type_id=type_id)
    records = q.order_by(BusIncidentRecord.incident_date, BusIncidentRecord.created_at).all()

    cfg = get_config()
    title = f"{cfg.app_name} — Incident Report ({d_from} to {d_to})"
    headers = ['Date','Bus ID','Bus Name','Route','Status','Delay (min)','Schedule','Notes','Recorded By']
    rows = [[
        r.incident_date.strftime('%Y-%m-%d'), r.bus.identifier, r.bus.name,
        r.bus.route or '', r.incident_type.name, r.delay_minutes,
        r.schedule_type.name if r.schedule_type else '',
        r.notes or '', r.created_by.username if r.created_by else '',
    ] for r in records]

    # ── Bus Audit for export ──────────────────────────────────────────────
    default_type_exp = IncidentType.query.filter_by(is_default=True).first()
    exp_buses_q = Bus.query.filter_by(active=True).order_by(Bus.identifier)
    if bus_id:
        exp_buses_q = exp_buses_q.filter_by(id=bus_id)
    exp_buses_list = exp_buses_q.all()
    total_days_exp = (d_to - d_from).days + 1
    bus_inc_dates_exp = {}
    for r in records:
        if not r.incident_type.is_default:
            bus_inc_dates_exp.setdefault(r.bus_id, set()).add(r.incident_date)
    audit_headers = ['Bus ID','Bus Name','Route','Total Days','On-Time Days',
                     'Incident Days','On-Time Rate (%)','Avg Delay (min)','Total Delay (min)']
    audit_rows = []
    for bus in exp_buses_list:
        inc_d = len(bus_inc_dates_exp.get(bus.id, set()))
        ot_d  = max(0, total_days_exp - inc_d)
        bdel  = [r.delay_minutes for r in records
                 if r.bus_id == bus.id and r.delay_minutes and r.delay_minutes > 0]
        avg_d = round(sum(bdel)/len(bdel), 1) if bdel else 0.0
        rate  = round(ot_d / total_days_exp * 100, 1) if total_days_exp else 100.0
        audit_rows.append([bus.identifier, bus.name, bus.route or '',
                           total_days_exp, ot_d, inc_d, rate, avg_d, sum(bdel)])

    if fmt == 'csv':
        output = io.StringIO()
        w = csv.writer(output)
        w.writerow(headers)
        w.writerows(rows)
        w.writerow([])
        w.writerow(['Bus Audit Summary'])
        w.writerow(audit_headers)
        w.writerows(audit_rows)
        resp = make_response(output.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = f'attachment; filename="bus_report_{d_from}_{d_to}.csv"'
        return resp

    elif fmt == 'pdf' and PDF_AVAILABLE:
        def _pdf_safe(text):
            return (str(text)
                    .replace('\u2014', '--').replace('\u2013', '-')
                    .replace('\u2018', "'").replace('\u2019', "'")
                    .replace('\u201c', '"').replace('\u201d', '"')
                    .encode('latin-1', errors='replace').decode('latin-1'))

        class _BusReportPDF(FPDF):
            def footer(self):
                self.set_y(-12)
                self.set_font('Helvetica', 'I', 7)
                self.set_text_color(150, 150, 150)
                self.cell(0, 5, 'Powered by Avidity Technologies Inc', align='C')
                self.set_text_color(0, 0, 0)

        pdf = _BusReportPDF(orientation='L', unit='mm', format='A4')
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.set_margins(10, 10, 10)
        pdf.add_page()

        # ── Header: logo + app name + report title ──────────────────────────
        logo_x = 10
        logo_fs = None
        if cfg.logo_path:
            candidate = os.path.join(BASE_DIR, cfg.logo_path.lstrip('/').replace('/', os.sep))
            if os.path.exists(candidate):
                logo_fs = candidate
        if logo_fs:
            try:
                pdf.image(logo_fs, x=logo_x, y=10, h=14)
                text_x = logo_x + 18
            except Exception:
                text_x = logo_x
        else:
            text_x = logo_x

        pdf.set_xy(text_x, 10)
        pdf.set_font('Helvetica', 'B', 15)
        pdf.set_text_color(30, 64, 175)
        pdf.cell(0, 8, _pdf_safe(cfg.app_name or 'Bus Tracker'), ln=True)
        pdf.set_x(text_x)
        pdf.set_font('Helvetica', '', 9)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 5, _pdf_safe(title), ln=True)
        pdf.set_text_color(0, 0, 0)

        # Separator line
        pdf.set_y(max(pdf.get_y(), 26))
        pdf.set_draw_color(226, 232, 240)
        pdf.line(10, pdf.get_y(), 287, pdf.get_y())
        pdf.ln(3)

        # ── Table header ────────────────────────────────────────────────────
        # A4 landscape usable width ≈ 277mm (297 - 2×10)
        col_widths = [28, 20, 42, 38, 34, 20, 27, 40, 28]
        pdf.set_font('Helvetica', 'B', 7)
        pdf.set_fill_color(30, 64, 175)
        pdf.set_text_color(255, 255, 255)
        for h, w in zip(headers, col_widths):
            pdf.cell(w, 7, _pdf_safe(h), border=0, fill=True, align='C')
        pdf.ln()

        # ── Table rows ──────────────────────────────────────────────────────
        pdf.set_font('Helvetica', '', 7)
        pdf.set_text_color(15, 23, 42)
        alt = False
        for row in rows:
            pdf.set_fill_color(241, 245, 249) if alt else pdf.set_fill_color(255, 255, 255)
            for val, w in zip(row, col_widths):
                pdf.cell(w, 6, _pdf_safe(str(val))[:35], border=0, fill=True)
            pdf.ln()
            alt = not alt

        # ── Bus Audit table ──────────────────────────────────────────────
        pdf.ln(6)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(30, 64, 175)
        pdf.cell(0, 6, 'Bus Audit Summary', ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)
        a_widths = [22, 45, 38, 22, 22, 22, 28, 28, 28]
        pdf.set_font('Helvetica', 'B', 7)
        pdf.set_fill_color(30, 64, 175)
        pdf.set_text_color(255, 255, 255)
        for h, w in zip(audit_headers, a_widths):
            pdf.cell(w, 7, _pdf_safe(h), border=0, fill=True, align='C')
        pdf.ln()
        pdf.set_font('Helvetica', '', 7)
        pdf.set_text_color(15, 23, 42)
        alt = False
        for row in audit_rows:
            pdf.set_fill_color(241, 245, 249) if alt else pdf.set_fill_color(255, 255, 255)
            for val, w in zip(row, a_widths):
                pdf.cell(w, 6, _pdf_safe(str(val))[:30], border=0, fill=True)
            pdf.ln()
            alt = not alt

        resp = make_response(bytes(pdf.output()))
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = f'attachment; filename="bus_report_{d_from}_{d_to}.pdf"'
        return resp

    elif fmt == 'docx' and DOCX_AVAILABLE:
        doc = DocxDocument()
        doc.add_heading(title, 0)
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = 'Table Grid'
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        for row in rows:
            cells = table.add_row().cells
            for i, val in enumerate(row):
                cells[i].text = str(val)
        doc.add_heading('Bus Audit Summary', level=2)
        a_table = doc.add_table(rows=1, cols=len(audit_headers))
        a_table.style = 'Table Grid'
        for i, h in enumerate(audit_headers):
            a_table.rows[0].cells[i].text = h
        for row in audit_rows:
            cells = a_table.add_row().cells
            for i, val in enumerate(row):
                cells[i].text = str(val)
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return send_file(buf, as_attachment=True,
                         download_name=f'bus_report_{d_from}_{d_to}.docx',
                         mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    flash(f'Export format "{fmt}" not available. Try CSV.', 'error')
    return redirect(url_for('statistics'))

@app.route('/admin/statistics/email', methods=['POST'])
@login_required
@require_module('statistics')
def email_statistics():
    to_email = request.form.get('email', '').strip()
    if not to_email:
        flash('Enter a recipient email.', 'error')
        return redirect(url_for('statistics'))
    cfg = get_config()
    configure_mail(cfg)
    try:
        today = date.today()
        records = BusIncidentRecord.query.filter(
            BusIncidentRecord.is_pending == False,
            BusIncidentRecord.incident_date == today,
        ).all()
        body = f"{cfg.app_name} — Daily Bus Report ({today})\n\n"
        body += f"{'Bus':<12} {'Status':<22} {'Delay':>6}  Schedule\n"
        body += '-' * 60 + '\n'
        for r in records:
            body += f"{r.bus.identifier:<12} {r.incident_type.name:<22} {r.delay_minutes:>5}m  {r.schedule_type.name if r.schedule_type else ''}\n"
        msg = Message(subject=f"Bus Report — {today}", recipients=[to_email], body=body)
        mail.send(msg)
        flash(f'Report sent to {to_email}.', 'success')
    except Exception as e:
        flash(f'Could not send email: {e}', 'error')
    return redirect(url_for('statistics'))


# ── USERS MODULE ──────────────────────────────────────────────────────────────

@app.route('/admin/users')
@login_required
@require_module('users')
def users():
    all_users  = User.query.order_by(User.username).all()
    all_groups = UserGroup.query.order_by(UserGroup.name).all()
    can_write  = current_user.has_access('users', 'full')
    return render_template('admin/users.html', users=all_users, groups=all_groups,
                           MODULES=MODULES, can_write=can_write)

@app.route('/admin/users/add', methods=['POST'])
@login_required
@require_module('users', 'full')
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    email    = request.form.get('email', '').strip() or None
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('users'))
    if User.query.filter_by(username=username).first():
        flash(f'Username "{username}" already exists.', 'error')
        return redirect(url_for('users'))
    if email and User.query.filter_by(email=email).first():
        flash('Email already in use.', 'error')
        return redirect(url_for('users'))
    u = User(username=username, email=email,
             first_name=request.form.get('first_name', '').strip() or None,
             last_name=request.form.get('last_name', '').strip() or None,
             phone=request.form.get('phone', '').strip() or None,
             workplace=request.form.get('workplace', '').strip() or None,
             job_title=request.form.get('job_title', '').strip() or None,
             group_id=request.form.get('group_id', type=int) or None,
             use_email_auth='use_email_auth' in request.form,
             receive_notifications='receive_notifications' in request.form,
             active=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash(f'User "{username}" created.', 'success')
    return redirect(url_for('users'))

@app.route('/admin/users/<int:uid>/edit', methods=['POST'])
@login_required
@require_module('users', 'full')
def edit_user(uid):
    u = User.query.get_or_404(uid)
    # Only admins can edit other users' group; everyone can edit own profile
    if uid != current_user.id and not current_user.is_admin:
        flash('Permission denied.', 'error')
        return redirect(url_for('users'))
    u.first_name  = request.form.get('first_name', '').strip() or None
    u.last_name   = request.form.get('last_name', '').strip() or None
    u.email       = request.form.get('email', '').strip() or None
    u.phone       = request.form.get('phone', '').strip() or None
    u.workplace   = request.form.get('workplace', '').strip() or None
    u.job_title   = request.form.get('job_title', '').strip() or None
    u.use_email_auth        = 'use_email_auth' in request.form
    u.receive_notifications = 'receive_notifications' in request.form
    if current_user.is_admin:
        u.group_id = request.form.get('group_id', type=int) or None
        u.active   = 'active' in request.form
    pwd = request.form.get('new_password', '').strip()
    if pwd: u.set_password(pwd)
    db.session.commit()
    flash(f'User "{u.username}" updated.', 'success')
    return redirect(url_for('users'))

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@login_required
@require_module('users', 'full')
def delete_user(uid):
    if not current_user.is_admin:
        flash('Only administrators can delete users.', 'error')
        return redirect(url_for('users'))
    u = User.query.get_or_404(uid)
    if u.username == 'admin':
        flash('Cannot delete the default admin account.', 'error')
        return redirect(url_for('users'))
    db.session.delete(u)
    db.session.commit()
    flash(f'User "{u.username}" deleted.', 'success')
    return redirect(url_for('users'))

@app.route('/admin/groups/add', methods=['POST'])
@login_required
@require_module('users', 'full')
def add_group():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Name is required.', 'error')
        return redirect(url_for('users'))
    if UserGroup.query.filter_by(name=name).first():
        flash('Group already exists.', 'error')
        return redirect(url_for('users'))
    g = UserGroup(name=name, description=request.form.get('description','').strip() or None)
    db.session.add(g)
    db.session.flush()
    for mod in MODULES:
        level = request.form.get(f'perm_{mod["key"]}', 'none')
        db.session.add(GroupPermission(group_id=g.id, module_key=mod['key'], access_level=level))
    db.session.commit()
    flash(f'Group "{name}" created.', 'success')
    return redirect(url_for('users'))

@app.route('/admin/groups/<int:gid>/edit', methods=['POST'])
@login_required
@require_module('users', 'full')
def edit_group(gid):
    g = UserGroup.query.get_or_404(gid)
    g.name        = request.form.get('name', g.name).strip()
    g.description = request.form.get('description', '').strip() or None
    if not g.is_admin:
        for mod in MODULES:
            level = request.form.get(f'perm_{mod["key"]}', 'none')
            perm  = GroupPermission.query.filter_by(group_id=gid, module_key=mod['key']).first()
            if perm: perm.access_level = level
            else: db.session.add(GroupPermission(group_id=gid, module_key=mod['key'], access_level=level))
    db.session.commit()
    flash(f'Group "{g.name}" updated.', 'success')
    return redirect(url_for('users'))

@app.route('/admin/groups/<int:gid>/delete', methods=['POST'])
@login_required
@require_module('users', 'full')
def delete_group(gid):
    if not current_user.is_admin:
        flash('Only administrators can delete groups.', 'error')
        return redirect(url_for('users'))
    g = UserGroup.query.get_or_404(gid)
    if g.is_admin:
        flash('Cannot delete the Administrator group.', 'error')
        return redirect(url_for('users'))
    if g.users:
        flash('Cannot delete: group has assigned users.', 'error')
        return redirect(url_for('users'))
    db.session.delete(g)
    db.session.commit()
    flash(f'Group "{g.name}" deleted.', 'success')
    return redirect(url_for('users'))


# ── NOTIFICATIONS MODULE ──────────────────────────────────────────────────────

@app.route('/admin/notifications')
@login_required
@require_module('notifications')
def notifications():
    subs        = NotificationSubscriber.query.order_by(NotificationSubscriber.last_name).all()
    groups      = SubscriberGroup.query.order_by(SubscriberGroup.name).all()
    all_buses   = Bus.query.filter_by(active=True).order_by(Bus.identifier).all()
    admin_users = User.query.filter_by(active=True).order_by(User.username).all()
    can_write   = current_user.has_access('notifications', 'full')
    return render_template('admin/notifications.html',
                           subscribers=subs, groups=groups,
                           all_buses=all_buses, admin_users=admin_users,
                           can_write=can_write)

@app.route('/admin/notifications/add', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def add_subscriber():
    s = NotificationSubscriber(
        first_name=request.form.get('first_name','').strip() or None,
        last_name=request.form.get('last_name','').strip() or None,
        email=request.form.get('email','').strip() or None,
        phone=request.form.get('phone','').strip() or None,
        group_id=request.form.get('group_id', type=int) or None,
    )
    db.session.add(s)
    db.session.flush()
    for bid in request.form.getlist('bus_ids'):
        db.session.add(NotificationBusAssignment(subscriber_id=s.id, bus_id=int(bid)))
    db.session.commit()
    flash(f'Subscriber "{s.full_name}" added.', 'success')
    return redirect(url_for('notifications'))

@app.route('/admin/notifications/<int:sid>/edit', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def edit_subscriber(sid):
    s = NotificationSubscriber.query.get_or_404(sid)
    s.first_name = request.form.get('first_name','').strip() or None
    s.last_name  = request.form.get('last_name','').strip() or None
    s.email      = request.form.get('email','').strip() or None
    s.phone      = request.form.get('phone','').strip() or None
    s.active     = 'active' in request.form
    s.group_id   = request.form.get('group_id', type=int) or None
    NotificationBusAssignment.query.filter_by(subscriber_id=sid).delete()
    for bid in request.form.getlist('bus_ids'):
        db.session.add(NotificationBusAssignment(subscriber_id=sid, bus_id=int(bid)))
    db.session.commit()
    flash('Subscriber updated.', 'success')
    return redirect(url_for('notifications'))

@app.route('/admin/notifications/<int:sid>/delete', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def delete_subscriber(sid):
    s = NotificationSubscriber.query.get_or_404(sid)
    db.session.delete(s)
    db.session.commit()
    flash('Subscriber removed.', 'success')
    return redirect(url_for('notifications'))


# ── SUBSCRIBER GROUPS ──────────────────────────────────────────────────────────

@app.route('/admin/notifications/groups/add', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def add_subscriber_group():
    name  = request.form.get('name', '').strip()
    color = request.form.get('color', 'blue').strip()
    desc  = request.form.get('description', '').strip()
    if not name:
        flash('Group name is required.', 'error')
        return redirect(url_for('notifications', tab='groups'))
    if SubscriberGroup.query.filter_by(name=name).first():
        flash(f'Group "{name}" already exists.', 'error')
        return redirect(url_for('notifications', tab='groups'))
    db.session.add(SubscriberGroup(name=name, color=color, description=desc))
    db.session.commit()
    flash(f'Group "{name}" created.', 'success')
    return redirect(url_for('notifications', tab='groups'))


@app.route('/admin/notifications/groups/<int:gid>/delete', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def delete_subscriber_group(gid):
    g = SubscriberGroup.query.get_or_404(gid)
    # Unassign subscribers from this group before deleting
    NotificationSubscriber.query.filter_by(group_id=gid).update({'group_id': None})
    db.session.delete(g)
    db.session.commit()
    flash(f'Group "{g.name}" deleted.', 'success')
    return redirect(url_for('notifications', tab='groups'))


@app.route('/admin/notifications/groups/<int:gid>/edit', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def edit_subscriber_group(gid):
    g = SubscriberGroup.query.get_or_404(gid)
    name = request.form.get('name', '').strip()
    if not name:
        flash('Group name is required.', 'error')
        return redirect(url_for('notifications', tab='groups'))
    existing = SubscriberGroup.query.filter_by(name=name).first()
    if existing and existing.id != gid:
        flash(f'Group "{name}" already exists.', 'error')
        return redirect(url_for('notifications', tab='groups'))
    g.name        = name
    g.color       = request.form.get('color', g.color)
    g.description = request.form.get('description', '').strip()
    db.session.commit()
    flash('Group updated.', 'success')
    return redirect(url_for('notifications', tab='groups'))


# ── BROADCAST ─────────────────────────────────────────────────────────────────

def _build_recipient_list(target, group_ids, subscriber_id, user_id):
    """Return list of (name, email) tuples based on target selection."""
    recipients = []
    seen = set()

    def add(name, email):
        if email and email not in seen:
            seen.add(email)
            recipients.append((name, email))

    if target in ('all', 'subscribers', 'group'):
        query = NotificationSubscriber.query.filter_by(active=True)
        if target == 'group' and group_ids:
            query = query.filter(NotificationSubscriber.group_id.in_(group_ids))
        for s in query.order_by(NotificationSubscriber.last_name).all():
            add(s.full_name, s.email)

    if target in ('all', 'admins'):
        for u in User.query.filter_by(active=True, receive_notifications=True).all():
            add(u.username, u.email)

    if target == 'individual_subscriber' and subscriber_id:
        s = NotificationSubscriber.query.get(subscriber_id)
        if s:
            add(s.full_name, s.email)

    if target == 'individual_user' and user_id:
        u = User.query.get(user_id)
        if u:
            add(u.username, u.email)

    return recipients


def _broadcast_worker(job_id, recipients, subject, body, interval_sec):
    """Background thread: send emails with optional interval between each."""
    broadcast_jobs[job_id].update({'total': len(recipients), 'sent': 0, 'failed': 0,
                                   'done': False, 'errors': []})
    with app.app_context():
        cfg = Configuration.query.first()
        if cfg:
            configure_mail(cfg)
        for i, (name, email) in enumerate(recipients):
            if i > 0 and interval_sec > 0:
                time.sleep(interval_sec)
            try:
                msg = Message(subject=subject, recipients=[email],
                              body=f"Hi {name or 'there'},\n\n{body}")
                mail.send(msg)
                broadcast_jobs[job_id]['sent'] += 1
            except Exception as e:
                broadcast_jobs[job_id]['failed'] += 1
                broadcast_jobs[job_id]['errors'].append(f'{email}: {str(e)[:120]}')
        broadcast_jobs[job_id]['done'] = True


@app.route('/admin/notifications/broadcast', methods=['POST'])
@login_required
@require_module('notifications', 'full')
def send_broadcast():
    data         = request.get_json(silent=True) or {}
    target       = data.get('target', 'all')
    group_ids    = [int(x) for x in data.get('group_ids', []) if x]
    sub_id       = data.get('subscriber_id')
    user_id      = data.get('user_id')
    subject      = (data.get('subject') or '').strip()
    body         = (data.get('body') or '').strip()
    interval_sec = max(0, int(data.get('interval', 0)))

    if not subject or not body:
        return jsonify({'ok': False, 'message': 'Subject and body are required.'})

    recipients = _build_recipient_list(target, group_ids, sub_id, user_id)
    if not recipients:
        return jsonify({'ok': False, 'message': 'No valid recipients found.'})

    job_id = str(uuid.uuid4())[:8]
    broadcast_jobs[job_id] = {'total': len(recipients), 'sent': 0, 'failed': 0,
                               'done': False, 'errors': []}

    t = threading.Thread(target=_broadcast_worker,
                         args=(job_id, recipients, subject, body, interval_sec),
                         daemon=True)
    t.start()
    return jsonify({'ok': True, 'job_id': job_id, 'total': len(recipients)})


@app.route('/admin/notifications/broadcast/<job_id>/status')
@login_required
def broadcast_status(job_id):
    job = broadcast_jobs.get(job_id)
    if not job:
        return jsonify({'done': True, 'sent': 0, 'failed': 0, 'total': 0, 'errors': []})
    return jsonify(job)


# ── CONFIGURATION MODULE ──────────────────────────────────────────────────────

@app.route('/admin/config', methods=['GET', 'POST'])
@login_required
@require_module('config')
def config_page():
    cfg = get_config()
    if request.method == 'POST':
        section = request.form.get('section', 'general')
        if section == 'general':
            cfg.app_name     = request.form.get('app_name', cfg.app_name).strip()
            cfg.app_subtitle = request.form.get('app_subtitle', cfg.app_subtitle).strip()
            cfg.time_format  = request.form.get('time_format', '12h')
        elif section == 'theme':
            cfg.theme_mode     = request.form.get('theme_mode', 'light')
            cfg.color_bg       = request.form.get('color_bg', cfg.color_bg)
            cfg.color_nav      = request.form.get('color_nav', cfg.color_nav)
            cfg.color_card     = request.form.get('color_card', cfg.color_card)
            cfg.color_text     = request.form.get('color_text', cfg.color_text)
            cfg.color_accent   = request.form.get('color_accent', cfg.color_accent)
            cfg.color_nav_text = request.form.get('color_nav_text', cfg.color_nav_text)
        elif section == 'operational':
            cfg.timezone       = request.form.get('timezone', cfg.timezone)
            cfg.daily_reset_time = request.form.get('daily_reset_time', cfg.daily_reset_time)
            cfg.commit_delay_min = request.form.get('commit_delay_min', cfg.commit_delay_min, type=int)
            cfg.offline_message  = request.form.get('offline_message', cfg.offline_message)
            cfg.show_always      = 'show_always' in request.form
        elif section == 'schedule_windows':
            for p in BusScheduleType.query.all():
                w_start = request.form.get(f'window_start_{p.id}', '').strip()
                w_end   = request.form.get(f'window_end_{p.id}',   '').strip()
                p.window_start = w_start or None
                p.window_end   = w_end   or None
        elif section == 'language':
            cfg.lang_frontend = request.form.get('lang_frontend', 'en')
            cfg.lang_backend  = request.form.get('lang_backend', 'en')
        elif section == 'email':
            cfg.mail_provider   = request.form.get('mail_provider', 'custom')
            cfg.mail_server     = request.form.get('mail_server', '').strip()
            cfg.mail_port       = request.form.get('mail_port', 587, type=int)
            cfg.mail_use_tls    = 'mail_use_tls' in request.form
            cfg.mail_use_ssl    = 'mail_use_ssl' in request.form
            cfg.mail_username   = request.form.get('mail_username', '').strip()
            new_pwd = request.form.get('mail_password', '').strip()
            if new_pwd:
                cfg.mail_password = new_pwd
            cfg.mail_from_email = request.form.get('mail_from_email', '').strip()
            cfg.mail_from_name  = request.form.get('mail_from_name', '').strip()
        db.session.commit()
        flash('Configuration saved.', 'success')
        return redirect(url_for('config_page', tab=section))

    # Operational schedules and holidays
    schedules      = OperationalSchedule.query.order_by(OperationalSchedule.name).all()
    holidays       = Holiday.query.order_by(Holiday.holiday_date.desc()).all()
    schedule_types = BusScheduleType.query.order_by(BusScheduleType.sort_order).all()
    timezones      = ['America/New_York','America/Chicago','America/Denver',
                      'America/Los_Angeles','America/Anchorage','Pacific/Honolulu',
                      'America/Puerto_Rico','Europe/London','Europe/Madrid']
    active_tab = request.args.get('tab', 'general')
    can_write  = current_user.has_access('config', 'full')
    return render_template('admin/config.html', cfg=cfg, schedules=schedules,
                           holidays=holidays, schedule_types=schedule_types,
                           timezones=timezones, active_tab=active_tab, can_write=can_write)

@app.route('/admin/config/upload-logo', methods=['POST'])
@login_required
@require_module('config', 'full')
def upload_logo():
    cfg  = get_config()
    field = request.form.get('field', 'logo')
    f = request.files.get('file')
    if f and allowed_file(f.filename):
        fn = secure_filename(f'app_{field}_{f.filename}')
        f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        if field == 'logo': cfg.logo_path = f'/static/uploads/{fn}'
        else:               cfg.icon_path = f'/static/uploads/{fn}'
        db.session.commit()
        flash('File uploaded.', 'success')
    return redirect(url_for('config_page', tab='general'))

@app.route('/admin/config/test-email', methods=['POST'])
@login_required
@require_module('config', 'full')
def test_email():
    cfg = get_config()
    configure_mail(cfg)
    to = request.form.get('test_email', current_user.email or '')
    try:
        msg = Message(subject=f'Test Email — {cfg.app_name}',
                      recipients=[to],
                      body=f'This is a test email from {cfg.app_name}.')
        mail.send(msg)
        flash(f'Test email sent to {to}.', 'success')
    except Exception as e:
        flash(f'Email failed: {e}', 'error')
    return redirect(url_for('config_page', tab='email'))


@app.route('/admin/config/test-email-live', methods=['POST'])
@login_required
@require_module('config', 'full')
def test_email_live():
    """AJAX endpoint: test SMTP with current form values (does not save to DB)."""
    data = request.get_json(silent=True) or {}
    test_to = data.get('test_to', '').strip()
    if not test_to:
        return jsonify({'ok': False, 'message': 'Recipient email is required.'})
    cfg = get_config()
    override = {
        'provider':   data.get('provider', 'custom'),
        'server':     data.get('server', ''),
        'port':       data.get('port', 587),
        'use_tls':    data.get('use_tls', True),
        'use_ssl':    data.get('use_ssl', False),
        'username':   data.get('username', ''),
        'password':   data.get('password', '') or cfg.mail_password,
        'from_email': data.get('from_email', ''),
        'from_name':  data.get('from_name', ''),
    }
    try:
        configure_mail(cfg, override=override)
        msg = Message(
            subject=f'Test Email — {cfg.app_name}',
            recipients=[test_to],
            body=(f'This is a test email from {cfg.app_name}.\n\n'
                  f'SMTP: {app.config.get("MAIL_SERVER")}:{app.config.get("MAIL_PORT")}\n'
                  f'TLS: {app.config.get("MAIL_USE_TLS")}  SSL: {app.config.get("MAIL_USE_SSL")}\n'
                  f'From: {app.config.get("MAIL_DEFAULT_SENDER")}'),
        )
        mail.send(msg)
        return jsonify({'ok': True, 'message': f'Test email sent successfully to {test_to}.'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})

@app.route('/admin/config/check-smtp', methods=['POST'])
@login_required
@require_module('config', 'full')
def check_smtp():
    """AJAX endpoint: step-by-step SMTP diagnostics using smtplib directly."""
    import smtplib, socket as _socket
    data = request.get_json(silent=True) or {}
    cfg  = get_config()

    PRESET_SERVERS = {'google': 'smtp.gmail.com', 'office365': 'smtp.office365.com'}
    provider = data.get('provider', 'custom')
    server   = PRESET_SERVERS.get(provider) or data.get('server', cfg.mail_server) or ''
    port     = int(data.get('port', None) or cfg.mail_port or 587)
    use_tls  = bool(data.get('use_tls', cfg.mail_use_tls))
    use_ssl  = bool(data.get('use_ssl', getattr(cfg, 'mail_use_ssl', False)))
    username = data.get('username', cfg.mail_username) or ''
    password = data.get('password', '') or cfg.mail_password or ''

    steps = []

    if not server:
        return jsonify({'ok': False, 'steps': [
            {'ok': False, 'label': 'SMTP Server not configured',
             'detail': 'Enter a server hostname or select a preset provider.'}]})

    # Step 1 — TCP connection
    try:
        sock = _socket.create_connection((server, port), timeout=10)
        sock.close()
        steps.append({'ok': True,  'label': f'TCP connect to {server}:{port}'})
    except _socket.timeout:
        steps.append({'ok': False, 'label': f'TCP connect to {server}:{port}',
                      'detail': 'Connection timed out — server unreachable or port blocked by firewall/ISP.'})
        return jsonify({'ok': False, 'steps': steps})
    except ConnectionRefusedError:
        steps.append({'ok': False, 'label': f'TCP connect to {server}:{port}',
                      'detail': 'Connection refused — wrong server/port, or a local firewall is blocking Python.'})
        return jsonify({'ok': False, 'steps': steps})
    except Exception as e:
        steps.append({'ok': False, 'label': f'TCP connect to {server}:{port}', 'detail': str(e)})
        return jsonify({'ok': False, 'steps': steps})

    # Step 2 — SMTP handshake
    smtp = None
    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(server, port, timeout=15)
        else:
            smtp = smtplib.SMTP(server, port, timeout=15)
        smtp.ehlo()
        steps.append({'ok': True, 'label': f'SMTP handshake (EHLO) — {"SSL" if use_ssl else "plain"}'})
    except Exception as e:
        steps.append({'ok': False, 'label': 'SMTP handshake', 'detail': str(e)})
        return jsonify({'ok': False, 'steps': steps})

    # Step 3 — STARTTLS
    if use_tls and not use_ssl:
        try:
            smtp.starttls()
            smtp.ehlo()
            steps.append({'ok': True, 'label': 'STARTTLS upgrade'})
        except Exception as e:
            steps.append({'ok': False, 'label': 'STARTTLS upgrade', 'detail': str(e)})
            smtp.quit()
            return jsonify({'ok': False, 'steps': steps})

    # Step 4 — Authentication
    if username and password:
        try:
            smtp.login(username, password)
            steps.append({'ok': True, 'label': f'Authentication ({username})'})
        except smtplib.SMTPAuthenticationError as e:
            detail = str(e)
            if '535' in detail or '5.7.8' in detail:
                detail += (' — For Gmail: use an App Password, not your regular password. '
                           'Go to Google Account → Security → App Passwords.')
            steps.append({'ok': False, 'label': f'Authentication ({username})', 'detail': detail})
            smtp.quit()
            return jsonify({'ok': False, 'steps': steps})
        except Exception as e:
            steps.append({'ok': False, 'label': f'Authentication ({username})', 'detail': str(e)})
            smtp.quit()
            return jsonify({'ok': False, 'steps': steps})
    else:
        steps.append({'ok': None, 'label': 'Authentication skipped — no credentials entered'})

    smtp.quit()
    return jsonify({'ok': True, 'steps': steps})


@app.route('/admin/config/schedules/add', methods=['POST'])
@login_required
@require_module('config', 'full')
def add_schedule():
    s = OperationalSchedule(
        name=request.form.get('name','').strip(),
        days=request.form.get('days','mon-fri'),
        start_time=request.form.get('start_time','07:00'),
        end_time=request.form.get('end_time','17:00'),
    )
    db.session.add(s)
    db.session.commit()
    flash('Schedule added.', 'success')
    return redirect(url_for('config_page', tab='operational'))

@app.route('/admin/config/schedules/<int:sid>/delete', methods=['POST'])
@login_required
@require_module('config', 'full')
def delete_schedule(sid):
    db.session.delete(OperationalSchedule.query.get_or_404(sid))
    db.session.commit()
    flash('Schedule removed.', 'success')
    return redirect(url_for('config_page', tab='operational'))

@app.route('/admin/config/holidays/add', methods=['POST'])
@login_required
@require_module('config', 'full')
def add_holiday():
    try:
        h = Holiday(
            name=request.form.get('name','').strip(),
            holiday_type=request.form.get('holiday_type','school'),
            holiday_date=date.fromisoformat(request.form.get('holiday_date','')),
            is_recurring='is_recurring' in request.form,
        )
        db.session.add(h)
        db.session.commit()
        flash('Holiday added.', 'success')
    except Exception:
        flash('Invalid date.', 'error')
    return redirect(url_for('config_page', tab='operational'))

@app.route('/admin/config/holidays/<int:hid>/delete', methods=['POST'])
@login_required
@require_module('config', 'full')
def delete_holiday(hid):
    db.session.delete(Holiday.query.get_or_404(hid))
    db.session.commit()
    flash('Holiday removed.', 'success')
    return redirect(url_for('config_page', tab='operational'))

@app.route('/admin/config/export-db')
@login_required
@require_module('config', 'full')
def export_db():
    db_path = os.path.join(BASE_DIR, 'bustrack.db')
    if os.path.exists(db_path):
        return send_file(db_path, as_attachment=True,
                         download_name=f'bustrack_backup_{date.today()}.db')
    flash('Database file not found.', 'error')
    return redirect(url_for('config_page', tab='general'))

@app.route('/admin/config/manual-commit', methods=['POST'])
@login_required
@require_module('config', 'full')
def manual_commit():
    commit_pending_incidents()
    flash('All pending incidents committed to statistics.', 'success')
    return redirect(url_for('config_page', tab='operational'))


# ── PROFILE ───────────────────────────────────────────────────────────────────

@app.route('/admin/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.first_name  = request.form.get('first_name','').strip() or None
        current_user.last_name   = request.form.get('last_name','').strip() or None
        current_user.email       = request.form.get('email','').strip() or None
        current_user.phone       = request.form.get('phone','').strip() or None
        current_user.workplace   = request.form.get('workplace','').strip() or None
        current_user.job_title   = request.form.get('job_title','').strip() or None
        current_user.use_email_auth = 'use_email_auth' in request.form
        current_user.receive_notifications = 'receive_notifications' in request.form
        pwd = request.form.get('new_password','').strip()
        if pwd:
            if not current_user.check_password(request.form.get('current_password','')):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('profile'))
            current_user.set_password(pwd)
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    return render_template('admin/profile.html')


# ── MAIN ─────────────────────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
