"""Development entry point for CloudVault (Stage 1).

Running this file initialises the database schema if needed and starts the
Flask development server. No application routes exist at this stage.
"""

from app import create_app, database

app = create_app()


if __name__ == "__main__":
    database.init_db(app.config["DATABASE"], app.config["SCHEMA_PATH"])
    app.run(debug=True)
