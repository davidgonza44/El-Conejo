"""Start the real application on loopback, explicitly targeting thesis, without dotenv."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seed_tesis_safety as safety


def main():
    from sqlalchemy import text
    url = safety.configured_url()
    domain = safety.load_domain()
    from app import create_app
    from app.config import Config

    class ThesisConfig(Config):
        SQLALCHEMY_DATABASE_URI = url

    app = create_app(ThesisConfig)
    with app.app_context():
        safety.require(domain.db.session.execute(text('SELECT DATABASE()')).scalar() == safety.DATABASE,
                       'Base efectiva incorrecta.')
        domain.db.session.remove()
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
