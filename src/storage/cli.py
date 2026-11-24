"""Credential-safe operations: python -m src.storage.cli --help."""
import argparse
import getpass
import json
import os

import psycopg
from .bootstrap import bootstrap, installation_status
from .migrations import migrate


def main(argv=None):
    parser=argparse.ArgumentParser(description='Dedicated tester PostgreSQL storage. Never pass secrets in command arguments.')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('doctor')
    commands.add_parser('migrate')
    create=commands.add_parser('bootstrap')
    create.add_argument('--database',default='tester_history')
    create.add_argument('--owner-role',default='tester_history_owner')
    create.add_argument('--runtime-role',default='tester_history_runtime')
    create.add_argument('--prompt-admin-password',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.command=='doctor':
            result=dict(tools=installation_status(),server_available=False)
            try:
                with psycopg.connect(os.getenv('TESTER_STORAGE_DSN',''),connect_timeout=3) as c:
                    result.update(server_available=True,server_version=c.info.server_version)
            except psycopg.Error: pass
            print(json.dumps(result)); return 0 if result['server_available'] else 1
        if args.command=='migrate':
            with psycopg.connect(os.getenv('TESTER_STORAGE_ADMIN_DSN',''),connect_timeout=8) as c: migrate(c)
            print('Storage migrations applied.'); return 0
        admin=os.getenv('TESTER_STORAGE_ADMIN_DSN','')
        if args.prompt_admin_password: admin=psycopg.conninfo.make_conninfo(admin,password=getpass.getpass('PostgreSQL administrator password: '))
        print(json.dumps(bootstrap(admin,args.database,args.owner_role,args.runtime_role)))
        return 0
    except Exception:
        # libpq/DDL exception text may contain connection details or password statements.
        print('Storage operation failed. Check server availability, OS credential store, dedicated-object ownership and privileges; no credentials were printed.')
        return 1


if __name__=='__main__': raise SystemExit(main())
