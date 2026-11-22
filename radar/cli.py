"""Admin command line:  python -m radar.cli migrate | create-user NAME --role ROLE | seed-demo"""
import argparse
import getpass
import sys

from . import auth, db, services


def main():
    p = argparse.ArgumentParser(prog="python -m radar.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create/upgrade the database schema")
    sub.add_parser("seed-demo", help="DEMO ONLY: wipe project data and load the synthetic project")
    cu = sub.add_parser("create-user", help="create a user (prompts for the password)")
    cu.add_argument("username")
    cu.add_argument("--role", required=True, choices=auth.ROLES)
    cu.add_argument("--name", default=None, help="full name")
    a = p.parse_args()
    c = db.connect()
    db.migrate(c)
    if a.cmd == "migrate":
        print("Database is up to date.")
    elif a.cmd == "seed-demo":
        db.reset_demo(c)
        services.recalc(c)
        print("Demo project loaded.")
    else:
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords differ.")
        auth.create_user(c, auth.SYSTEM, a.username, a.name or a.username, pw, a.role)
        print(f"User '{a.username}' created as {a.role}.")


if __name__ == "__main__":
    main()
