<h1 align="center">🥘 Supper</h1>

<p align="center">
  <strong>A self-hosted meal planner for households that can never decide what's for dinner.</strong>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-2E5B3E">
  <img alt="Flask" src="https://img.shields.io/badge/flask-3.1-2E5B3E">
  <img alt="SQLite" src="https://img.shields.io/badge/storage-SQLite-2E5B3E">
  <img alt="Proxmox LXC" src="https://img.shields.io/badge/proxmox-LXC-E8A92A">
  <img alt="Docker" src="https://img.shields.io/badge/docker-ready-E8A92A">
</p>


Supper plans your week for you. Press one button and every empty day gets a dinner, picked from 30
built-in recipes plus any you add yourself. Don't fancy something? Reroll it. Love it? Lock it.
When the week looks right, Supper turns it into a single combined shopping list.

It runs on your own server, needs no account, and keeps everything in one SQLite file. Everyone
in the house sees the same plan from their phone or laptop.

## Features

**Planning**
- Fill every empty day with one click, or shuffle the whole week (locked meals stay put)
- Reroll, lock, hand-pick or clear any single meal
- Mark days as *Leftovers* or *Eating out*
- Won't repeat anything you've had in the last two weeks
- Shared preferences: only pick certain tags, never pick others, a weeknight time limit,
  lean towards favourites, and optional lunch planning
- **Can't decide? Pick one** suggests a single dinner and puts it on tonight with one tap

**Recipes**
- 30 starter recipes (weeknight dinners, curries, pasta, traybakes, a Sunday roast), all in metric
- Add your own with tags, timings, servings, a photo link and private notes
- Search by name or ingredient, filter by tag, heart your favourites
- **Import from a link** on most recipe websites (anything publishing schema.org recipe data)
- **Import from pasted text** for family recipes, notes and emails
- Full backup and restore as a JSON file

**Shopping**
- Combines ingredients across the week (three recipes needing an onion become *3 onions*)
- Shows which meal each item is for
- Tick items off together, add extras like milk or bin bags, copy as text or print

**Everywhere**
- Works on phones, tablets and desktops
- Follows your device's light or dark mode
- No account, no tracking, no external services (apart from optional web fonts and link imports)


## Installation

Pick whichever suits your setup. All three store data in a single SQLite database.

### Option 1: Proxmox LXC container

Run this in your **Proxmox host's** shell. It creates an unprivileged Debian container, installs
Supper as a systemd service, and prints the address to open.

```bash
curl -fsSL https://github.com/Mrwhiskerss/supper/archive/refs/heads/main.tar.gz | tar xz
cd supper-main
bash proxmox/create-lxc.sh
```

The defaults are 1 core, 512 MB RAM, a 4 GB disk, DHCP on `vmbr0`, and start on boot. Change any
of them by setting it in front of the command:

```bash
CTID=150 STORAGE=local-zfs IP=192.168.1.50/24 GATEWAY=192.168.1.1 bash proxmox/create-lxc.sh
```

| Setting | Default | What it does |
| --- | --- | --- |
| `CTID` | next free ID | Container ID |
| `CT_HOSTNAME` | `supper` | Container hostname |
| `STORAGE` | `local-lvm` | Storage for the container's disk |
| `TEMPLATE_STORAGE` | `local` | Storage that holds LXC templates |
| `DISK` | `4` | Disk size in GB |
| `MEMORY` | `512` | RAM in MB |
| `CORES` | `1` | CPU cores |
| `BRIDGE` | `vmbr0` | Network bridge |
| `IP` | `dhcp` | Or a static address such as `192.168.1.50/24` |
| `GATEWAY` | | Required with a static IP |
| `PORT` | `8080` | Port Supper listens on |
| `ROOT_PASSWORD` | | Sets a root password (otherwise use `pct enter <id>`) |

**Already have a Debian or Ubuntu container?** Copy this repository into it and run
`bash proxmox/install.sh` as root.

### Option 2: Docker

```bash
git clone https://github.com/Mrwhiskerss/supper.git
cd supper
docker compose up -d --build
```

Data is stored in `./data`. To use a different port, change the left side of `ports:` in
`docker-compose.yml`, for example `"8090:8080"`.

### Option 3: Plain Python

Needs Python 3.11 or newer.

```bash
git clone https://github.com/Mrwhiskerss/supper.git
cd supper
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8080`.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `SUPPER_DB` | `./data/supper.db` | Path to the SQLite database |
| `PORT` | `8080` | Port when running with `python app.py` |

The database is created and filled with the starter recipes on first run.

## Updating

**Proxmox LXC:** download the new version on the host and run the updater with your container ID.
It backs up the database first and keeps all your recipes and plans.

```bash
curl -fsSL https://github.com/Mrwhiskerss/supper/archive/refs/heads/main.tar.gz | tar xz
cd supper-main
bash proxmox/update-lxc.sh <container-id>
```

**Docker:** `git pull && docker compose up -d --build`

## Backups

Everything lives in one file:

| Install | Database location |
| --- | --- |
| Proxmox LXC | `/var/lib/supper/supper.db` inside the container (included in Proxmox container backups) |
| Docker | `./data/supper.db` |
| Plain Python | `./data/supper.db`, or wherever `SUPPER_DB` points |

You can also download a portable backup from **Recipes → Import → Backup file**, and restore it on
any Supper install from the same place.

## Using Supper

- **Planning the week.** *Plan the empty days* fills gaps and leaves anything already planned.
  *Shuffle the week* replaces every meal that isn't locked. Use the lock on meals you've already
  shopped for.
- **Preferences.** Open *Planning preferences* above the week. They're saved on the server, so
  everyone in the house shares them.
- **Tags drive the planner.** Tag recipes with things like `quick`, `vegetarian` or `batch cook`,
  then use them in preferences ("only pick quick", "never pick fish").
- **Importing from a link.** Paste the address of a recipe page. You'll see an edit form to check
  the result before it's saved. Some sites block automated requests; for those, copy the recipe
  text and use *Paste text*.
- **Pasting text.** Put the recipe name on the first line. Lines under an *Ingredients* heading
  and a *Method* (or *Instructions*) heading are sorted automatically.

## Security

Supper has **no login**. Run it on your home network, or put it behind something that handles
access, such as a VPN (Tailscale, WireGuard) or a reverse proxy with authentication (Authelia,
Authentik, Caddy with basic auth). Don't expose it directly to the internet.

Importing from a link makes the server fetch the web address you give it, so the server needs
outbound internet access for that feature.

## Project structure

```
supper/
├── app.py               Flask app: API, planner, importers, shopping list
├── seed_recipes.py      The 30 starter recipes
├── static/index.html    The whole web interface (vanilla JS, no build step)
├── proxmox/
│   ├── create-lxc.sh    Creates a container and installs Supper (run on the Proxmox host)
│   ├── update-lxc.sh    Updates an existing container (run on the Proxmox host)
│   └── install.sh       Installs or updates Supper inside any Debian/Ubuntu system
├── Dockerfile
├── docker-compose.yml
└── requirements.txt     Flask and Gunicorn
```

<details>
<summary><strong>API reference</strong></summary>

All endpoints take and return JSON.

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/recipes` | List all recipes |
| `POST` | `/api/recipes` | Create a recipe |
| `PUT` | `/api/recipes/<id>` | Update a recipe |
| `DELETE` | `/api/recipes/<id>` | Delete a recipe |
| `POST` | `/api/recipes/<id>/favorite` | Toggle favourite |
| `GET` | `/api/plan?start=YYYY-MM-DD` | Get the week containing that date |
| `PUT` | `/api/plan/<day>/<meal>` | Set a meal: `recipe_id`, `note`, `locked` |
| `DELETE` | `/api/plan/<day>/<meal>` | Clear a meal |
| `POST` | `/api/plan/generate` | Fill (`mode: "fill"`) or shuffle (`mode: "shuffle"`) the week, or reroll one meal with `day` and `meal` |
| `POST` | `/api/suggest` | Suggest a single recipe |
| `GET` | `/api/shopping?start=YYYY-MM-DD` | Combined shopping list for a week |
| `POST` | `/api/import/url` | Parse a recipe from a web page (doesn't save it) |
| `POST` | `/api/import/text` | Parse a recipe from pasted text (doesn't save it) |
| `POST` | `/api/import/json` | Import recipes from a backup |
| `GET` | `/api/export` | Download a full backup |
| `GET` | `/healthz` | Health check |

`<meal>` is `lunch` or `dinner`.

</details>

## Contributing

Issues and pull requests are welcome. The app is deliberately small: one Python file, one HTML
file, and two dependencies. Please keep it that way where you can.

To work on it locally, follow the plain Python install above and refresh the browser after
changes to `static/index.html`. Restart `python app.py` after changes to the Python files.


