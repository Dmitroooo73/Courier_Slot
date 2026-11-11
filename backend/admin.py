from argparse import ArgumentParser
from os import getenv

from .pipeline.results import PostgresResults
from .services.auth import AuthStore


def main() -> None:
    parser = ArgumentParser(description="Назначить администратором зарегистрированный аккаунт.")
    parser.add_argument("login")
    arguments = parser.parse_args()
    connection = getenv("PULT_DATABASE_URL")
    if not connection:
        parser.error("Задайте PULT_DATABASE_URL.")
    if not AuthStore(PostgresResults(connection)).promote(arguments.login.strip().lower()):
        parser.error("Аккаунт не найден. Сначала зарегистрируйтесь в веб-пульте.")
    print("Роль администратора назначена.")


if __name__ == "__main__":
    main()
