"""Initialize the database — creates all tables."""
from src.database import engine, Base
import src.models  # noqa — ensure all models are registered


def init():
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")


if __name__ == "__main__":
    init()
