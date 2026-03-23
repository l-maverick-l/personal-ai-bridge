# Setup

## Windows quick start
1. Install Python 3.11 or newer.
2. Open PowerShell in the project folder.
3. Create a virtual environment.
4. Install dependencies with `pip install -r requirements.txt`.
5. Launch the app with `python -m app.main`.
6. Complete the first-run setup wizard.

## Yahoo Mail setup
1. Open Yahoo account security settings.
2. Generate a Yahoo app password for this app.
3. Enter your Yahoo email address and the Yahoo app password in the setup wizard or the Yahoo Mail tab.
4. Leave the default Yahoo IMAP/SMTP servers unless you have a specific reason to change them.
5. Click **Test Yahoo connection** before trying inbox search or sending mail.

## Current behavior
- Stores settings in a local SQLite database.
- Stores an application log file in the app data directory.
- Lets the user choose AI mode and approved folders.
- Connects to Yahoo Mail with IMAP for reading/search and SMTP for sending.
- Requires explicit confirmation before sending email.
- Still allows Yahoo mail listing/search/reading and file operations when AI is not configured.
