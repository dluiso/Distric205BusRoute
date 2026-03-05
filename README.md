# D205 Bus Route Tracker

A web application for publishing and managing school bus incident status in real time. Built for **District 205**, it provides a public-facing status board for parents and a full admin panel for staff.

---

## Features

### Public Page
- Real-time bus status board (On Time, Delayed, Out of Service, etc.)
- **Period-aware display** — automatically shows only buses operating in the current time window (Morning, Midday, Afternoon)
- Active period banner with time range
- Dark mode / Light mode toggle (persists via localStorage)
- 12h / 24h time format (configurable from admin)
- Bus schedule, route, capacity and incident details per card
- Favorites system (stored locally per browser)
- Search and filter by status, schedule period

### Admin Panel
- **Dashboard** — daily summary cards, incidents by type chart (bar/donut/pie/line), incidents by bus, trend over time
- **Bus Management** — register buses, assign schedule periods (Morning/Midday/Afternoon) with departure times, record incidents per period
- **Incident Types** — configurable status types with color, icon, and description
- **Statistics** — filterable incident history, charts by type/bus/day/period, export to PDF / CSV / DOCX, email report
- **Users & Groups** — role-based access control with per-module permissions (full / limited / none)
- **Notification Subscribers** — manage subscriber groups and bus assignments; email notifications sent only to subscribers of the affected bus
- **Configuration** — app name/logo/icon, theme colors, timezone, operational schedules, period time windows, holidays, email (SMTP/Gmail/Outlook), language (EN/ES), time format

### Security
- CSRF protection on all POST endpoints
- Rate-limited login (5 attempts / 5 min per IP)
- Session hardening (HttpOnly, SameSite, Secure in production)
- Security headers (X-Frame-Options, X-Content-Type-Options, etc.)
- Open-redirect prevention
- Installation wizard lock — wizard is permanently disabled after first install

### Deployment
- Docker + PostgreSQL (recommended for production)
- SQLite (zero-config for development/small installs)
- Gunicorn WSGI server in Docker
- Health check endpoint at `/health`

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, Flask 2.3+, Flask-SQLAlchemy, Flask-Login, Flask-Mail |
| Database | SQLite (dev) / PostgreSQL 15 (production) |
| Scheduler | APScheduler (auto-commit pending incidents) |
| Frontend | Tailwind CSS (Play CDN), Alpine.js v3, Chart.js, Font Awesome 6 |
| Export | fpdf2 (PDF), python-docx (DOCX), csv (built-in) |
| Server | Gunicorn (production), Flask dev server (development) |

---

## Quick Start with Docker (Recommended)

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/dluiso/Distric205BusRoute.git
cd Distric205BusRoute

# 2. Create your environment file
cp .env.example .env
```

Edit `.env` and set a strong secret key:

```env
SECRET_KEY=your-very-long-random-secret-key-here
DB_NAME=bustrack
DB_USER=bususer
DB_PASS=a-strong-database-password
```

Generate a secure key with:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

```bash
# 3. Start the services
docker-compose up -d

# 4. Open the app in your browser
# http://localhost:5000
# You will be redirected to the installation wizard automatically
```

### Docker Commands

```bash
# View logs
docker-compose logs -f web

# Stop services
docker-compose down

# Stop and remove all data (including database volume)
docker-compose down -v

# Rebuild after code changes
docker-compose up -d --build
```

### SQLite alternative (no database service needed)

Edit `docker-compose.yml` — comment out the `db` service and its volume, then change the web environment:

```yaml
DATABASE_URL: "sqlite:////app/instance/bustrack.db"
```

---

## Manual Installation on Linux (Python)

### Prerequisites

- Python 3.11 or newer
- pip
- (Optional) PostgreSQL if not using SQLite

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/dluiso/Distric205BusRoute.git
cd Distric205BusRoute

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the instance directory and environment file
mkdir -p instance
cp .env.example instance/.env
```

Edit `instance/.env`:

```env
SECRET_KEY=your-very-long-random-secret-key-here
# For SQLite (simplest):
DATABASE_URL=sqlite:///bustrack.db
# For PostgreSQL:
# DATABASE_URL=postgresql://bususer:buspass@localhost:5432/bustrack
```

```bash
# 5. Run the application
python app.py

# Open http://localhost:5000 — the installation wizard will appear
```

### Running with Gunicorn (production)

```bash
# Install gunicorn (already in requirements.txt)
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Running as a systemd service

Create `/etc/systemd/system/busroute.service`:

```ini
[Unit]
Description=D205 Bus Route Tracker
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/Distric205BusRoute
ExecStart=/opt/Distric205BusRoute/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always
EnvironmentFile=/opt/Distric205BusRoute/instance/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable busroute
sudo systemctl start busroute
sudo systemctl status busroute
```

### Nginx reverse proxy (optional but recommended)

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /opt/Distric205BusRoute/static/;
    }
}
```

---

## Installation Wizard

On first launch, the app automatically redirects to the **Installation Wizard** at `/install`.

The wizard guides you through:

1. **Database Configuration** — choose SQLite (zero config) or PostgreSQL (enter host, port, database name, user, password) and test the connection before proceeding
2. **Admin Account** — set the username, email, and password for the initial administrator
3. **Review & Install** — summary of settings, then one-click install

After successful installation, the wizard is **permanently locked** — the `/install` route becomes inaccessible to prevent unauthorized reconfiguration.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session signing key (must be long and random) | Auto-generated (not persistent) |
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///bustrack.db` |
| `FLASK_ENV` | Set to `production` to enable secure cookies | `development` |
| `PORT` | Port to expose (Docker only) | `5000` |
| `DB_NAME` | PostgreSQL database name (Docker only) | `bustrack` |
| `DB_USER` | PostgreSQL user (Docker only) | `bususer` |
| `DB_PASS` | PostgreSQL password (Docker only) | `buspass` |

> **Important:** Never commit your real `.env` file. It is listed in `.gitignore`.

---

## Project Structure

```
D205BusRoute/
├── app.py                          # Main Flask app (models, routes, logic)
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Docker image definition
├── docker-compose.yml              # Docker Compose (web + PostgreSQL)
├── .env.example                    # Environment variable template
├── .gitignore
├── .dockerignore
├── run.bat                         # Windows quick-start script
├── static/
│   ├── uploads/                    # App logo and icon (gitignored)
│   └── exports/                    # Generated export files (gitignored)
└── templates/
    ├── public/
    │   └── index.html              # Public bus status page
    ├── admin/
    │   ├── base.html               # Admin layout (sidebar, CSRF, dark mode)
    │   ├── login.html
    │   ├── dashboard.html
    │   ├── buses.html
    │   ├── incidents.html
    │   ├── users.html
    │   ├── notifications.html
    │   ├── statistics.html
    │   ├── config.html
    │   └── profile.html
    ├── install/
    │   └── wizard.html             # Installation wizard
    └── errors/
        ├── 403.html
        ├── 404.html
        └── 500.html
```

---

## Default Schedule Periods

| Period | Default Window | Color in charts |
|--------|---------------|-----------------|
| Morning | 06:00 – 11:30 | Orange |
| Midday | 11:30 – 14:00 | Blue |
| Afternoon | 14:00 – 19:00 | Purple |

Time windows are fully configurable from **Admin → Configuration → Operational → Period Time Windows**.

---

## License

This project is proprietary software developed for District 205.

---

*Powered by [SmartFiche](https://smartfiche.com)*
