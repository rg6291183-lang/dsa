# Forge

Forge is a lightweight social and professional networking web app for students and job creators. It is a single-file Flask application that serves both the JSON backend and the full single-page frontend, and stores everything in a JSON file on disk. It is set up to run on PythonAnywhere.

## Features

- **Accounts**: sign up as a *Student* or a *Job Creator* (username, password, name, email, course, year, bio, skills), log in and out, and edit your profile
- **Profile**: profile picture, bio, skills, followers / following / post counts, "My Posts" and "Reposts" views
- **Posts**: text posts (1 to 500 characters) with an optional photo (up to 25 MB) or video (up to 250 MB), plus likes, comments, reposts and delete
- **Post preview and sharing**: full-page post preview, and a "Send Post" sheet to share a post with people you follow
- **Following**: follow other members; follower counts are derived from other users' connection lists
- **Chat**: direct messages between members, including photo messages (up to 10 MB)
- **Online status**: a heartbeat marks users as online if they were seen within the last 180 seconds
- **Notifications**: a simple notification list shown on the Home tab
- **Jobs**: create and browse job postings (title, company, location, skills)
- **Search**: search members by name, role, skills and course from the Home tab
- **Appearance**: light and dark theme, saved in the browser
- **Multi-account switching**: save several accounts on one device and switch between them in Settings

## Tech Stack

- **Backend**: Python 3, Flask, Flask-CORS
- **Frontend**: plain HTML, CSS and vanilla JavaScript, embedded in the Python file as `HTML_PAGE` and rendered with `render_template_string`
- **Storage**: a JSON file (`forge_network_data.json`) plus an `uploads/` folder for media

## Project Structure

The app lives in one Python file, plus files it creates at runtime:

```
mysite/
├── flask_app.py                # the application (file name may differ)
├── forge_network_data.json     # created automatically: users, posts, jobs, messages, notifications
├── .forge_reset_v5_done        # flag file so the data reset runs only once
└── uploads/                    # uploaded images and videos, served at /media/<filename>
```

## Installation

```bash
pip install flask flask-cors
```

## Configuration

Paths are defined near the top of the file:

```python
project_home = "/home/Nidishwar/mysite"
```

Change `project_home` to the directory where you deploy the app. The data file, uploads folder and reset flag are all created inside it.

| Setting | Default | Meaning |
|---|---|---|
| `MAX_POST_CHARS` | 500 | Maximum characters in a post |
| `MAX_IMAGE_BYTES` | 25 MB | Maximum post image size |
| `MAX_CHAT_IMAGE_BYTES` | 10 MB | Maximum chat image size |
| `MAX_VIDEO_BYTES` | 250 MB | Maximum post video size |
| `ONLINE_WINDOW_SECONDS` | 180 | How long after last activity a user counts as online |
| `BLOCKED_JOB_TITLES` | 3 titles | Job titles that are filtered out |

The frontend also has an API base fallback of `https://nidishwar.pythonanywhere.com`, used when the page is not served over HTTP(S). Update it to your own domain.

## Running

### Locally

Add this to the bottom of the file if it is not already there, and adjust `project_home` to a local folder:

```python
if __name__ == "__main__":
    app.run(debug=True)
```

Then run:

```bash
python flask_app.py
```

and open `http://127.0.0.1:5000`.

### On PythonAnywhere

1. Upload the file to `/home/<username>/mysite/`.
2. In the **Web** tab, create a Flask web app and point the WSGI file at this module's `app` object.
3. Reload the web app.

## Data Model

All data is stored in one JSON object with these keys:

| Key | Type | Description |
|---|---|---|
| `users` | object | Map of username to user record (name, email, role, avatar, course, year, bio, skills, connections, last_seen) |
| `logged_in_user` | string or null | Currently logged-in user |
| `posts` | list | Feed posts |
| `jobs` | list | Job postings |
| `messages` | list | Chat messages |
| `notifications` | list | Notification entries (`id`, `name`, `username`) |

Missing keys or wrongly typed values are repaired automatically on load, and a corrupted data file is replaced with the defaults.

## Media Handling

Uploads are accepted either as multipart files or as base64 `data:` URLs. Each file is checked for allowed MIME type and size, given a random UUID filename, saved in `uploads/`, and referenced by the URL `/media/<filename>`.

## Known Limitations

- **First-run reset**: on first start, `reset_saved_data_once()` wipes all stored data and writes `.forge_reset_v5_done`. Delete that flag file only if you want to wipe the data again; change the version in its name to trigger a new reset.
- **Security**: this is a prototype. The frontend keeps saved accounts, including passwords, in the browser's `localStorage` to allow account switching, and the backend is not hardened (check that passwords are hashed and that endpoints authenticate the caller before any real deployment).
- **Storage**: a single JSON file is rewritten on every change and is not safe for concurrent writers or large datasets. Consider SQLite or PostgreSQL for production.
- **Uploads**: media files are stored on local disk and are never cleaned up automatically.

## License

No license specified. Add one before sharing or distributing.

