# Troubleshooting

## The app does not start
- Make sure Python 3.11+ is installed.
- Make sure PySide6 installed successfully from `requirements.txt`.

## Yahoo says login failed
- Use a Yahoo app password, not your normal Yahoo account password.
- Confirm the Yahoo email address matches the account that generated the app password.
- Use the built-in **Test Yahoo connection** button after saving settings.

## Yahoo inbox loading fails
- Confirm the IMAP server is `imap.mail.yahoo.com` and the default port is `993`.
- Check your internet connection.
- If Yahoo temporarily refuses the connection, wait a moment and try again.

## Sending email fails
- Confirm the SMTP server is `smtp.mail.yahoo.com` and the default port is `465`.
- Make sure you clicked **Confirm action** after requesting a send.
- Check that the recipient address looks valid.

## My settings did not save
- Confirm the app data folder is writable.
- Restart the app and reopen the setup wizard to verify saved values.

## Folder selection is blocked
- The registry rejects duplicate paths and invalid paths.
- Network and removable-drive edge cases can be expanded in later phases.
