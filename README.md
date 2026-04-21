# Infinity Tool

Infinity Tool is a standalone CLI to update Avaya Infinity user settings (e.g. agent ring time, max missed interactions) via `core-config-service` and `core/v4/users`.


## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp properties.example.json properties.json
# Edit properties.json — never commit real passwords
```

## Usage

```bash
python3 update_user_settings.py --dump-user YOUR_USER_ID
python3 update_user_settings.py --all-users --ring-time 25 --max-missed 3 --dry-run
python3 update_user_settings.py --all-users --ring-time 25 --max-missed 3
python3 update_user_settings.py --email user@example.com --set attributes.agent.ringTime=30
python3 update_queue_settings.py --list-queues
python3 update_queue_settings.py --dump-queue YOUR_QUEUE_ID
python3 update_queue_settings.py --queue-id YOUR_QUEUE_ID --set attributes.queue.autoPauseCount=3 --dry-run
```

### Queues (`update_queue_settings.py`)

- Dotted field names (`config.xxx`, `attributes.xxx`) are sent as **nested JSON** (e.g. `config.outboundCallerId` becomes `"config": { "outboundCallerId": "..." }`), not a single key with a dot in the name.
- **Folder of queues** (`--queues-dir PATH`): merge all `*.json` in that folder (batch-style `{ "defaults", "targets" }`, array of queue ids or targets, single target object, or `{ "queueIds": [...] }`) plus optional `queues.txt` or `queue_ids.txt` (one queue id per line, `#` comments ok). CLI `--set` / `--outbound-caller-id` are merged into defaults (CLI wins on duplicate keys).
- List queues: `python3 update_queue_settings.py --list-queues`
- List personal user queues only: `python3 update_queue_settings.py --list-queues --personal-user-queues`
- List personal user queues in one folder: `python3 update_queue_settings.py --list-queues --personal-user-queues --folder-id YOUR_FOLDER_ID`
- List **queue folders** (GET `core/v4/folders/queues`; prints TSV from `folders[]`: `id`, `parentFolderId`, `displayName`):  
  `python3 update_queue_settings.py --list-folders`  
  Optional: `--parent-folder-id SOME_ID` for `parentFolderId` in the query (default is `null`).
- **Folder-scoped updates** (`--folder-id FOLDER_ID`): limit `--all-queues` to queues in that folder, or require `--name` / `--queue-id` (and targets in `--batch` / `--queues-dir`) to match that folder. Use `--folder-id root` for queues not assigned to a folder.
- **Personal user queue scope** (`--personal-user-queues`): limit updates to personal/user queues only. Can be combined with `--folder-id` to target personal queues in one folder.
- Bulk update all queues (preview, no PUT):  
  `python3 update_queue_settings.py --all-queues --dry-run --set KEY=VALUE`  
  Same for one folder only: add `--folder-id YOUR_FOLDER_ID` (or `root`).  
  Same for personal user queues only: add `--personal-user-queues`.  
  Add more fields with repeated `--set`. Remove `--dry-run` to apply.
- Outbound caller ID — either explicit `--set` (quote so `+` is kept):  
  `--set 'config.outboundCallerId=+17189355100'`  
  or shorthand:  
  `--outbound-caller-id "+17189355100"`
- Journey tab default shorthand: `--journey-tab` (sets `config.tabsDefault` to `["journey"]`).
- Journey history–related `config.*` fields (examples):  
  `config.interactionHistoryType`, `config.interactionHistoryKey`, `config.interaction.HistoryTable` — pass each with `--set KEY=VALUE`.

  ##EXAMPLE PERSONAL QUEUES UPDATE DRY-RUN:
  python3 update_queue_settings.py \
  --all-queues \
  --personal-user-queues \
  --set config.interactionHistoryType=sourceDetails \
  --set config.interactionHistoryKey=phoneNumber \
  --set config.interaction.HistoryTable=phoneNumber \
  --dry-run

- Default config path: `./properties.json` (override with `--properties`).
- Optional env: `INFINITY_ATTR_RING_TIME`, `INFINITY_ATTR_MAX_MISSED` for API field names.

## VS Code

Open the repo folder in [Visual Studio Code](https://code.visualstudio.com/), install the **Python** extension, select the `.venv` interpreter (Command Palette → *Python: Select Interpreter*), then **Run and Debug** (`F5`) and pick a launch configuration. Edit `args` in `.vscode/launch.json` as needed.

## Sabrina Tripaylan
