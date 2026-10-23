# Revenue Intelligence Inbox

A lightweight app for reviewing sales calls and meetings. It summarizes transcript-backed risks, follow-ups, and route decisions, with source evidence available for review.

**Live app:** <https://revenue-intelligence-inbox-7smc.onrender.com>

## Run locally

Requires Python 3.11 or newer.

```powershell
python -m pip install -r requirements.txt
python -m uvicorn inbox.api:app --host 127.0.0.1 --port 8000
```

After starting the local server, open it in a browser. On startup, the app loads the supplied sample records.

## Use the app

- Review records, transcript evidence, risk summaries, and suggested routes.
- Ask the assistant questions about the loaded records.
- Use **Add data** to import a JSON delivery that follows the [data contract](DATA_CONTRACT.md).
- Export the current records and analyses from the app.

New deliveries need a batch ID and revision. Higher revisions replace the source record; manual route decisions are kept. New records without a checked analysis are marked for human review.

## Public Render deployment

The root [render.yaml](render.yaml) configures a free Render Docker service. Connect the GitHub repository to Render as a Blueprint and deploy the `main` branch. The app is currently configured without authentication: anyone with the URL can view records, import data, and change route decisions.

The sample data will be publicly accessible. Render's free service can sleep when idle and does not provide persistent disk storage, so saved SQLite data and exports may be lost after a restart, sleep, or redeploy.

## Development

Run the tests with:

```powershell
python -m pytest -q
```
