"""Non-destructive dedicated database provisioning. Secrets stay in OS credential storage."""
import re
import secrets
import shutil

import psycopg
from psycopg import sql
from .migrations import migrate
from .models import digest


class SecretStore:
    def __init__(self):
        import keyring
        backend=keyring.get_keyring()
        # Reject plaintext, fail, null and user-installed fallback backends.
        if type(backend).__module__ not in ('keyring.backends.Windows','keyring.backends.macOS','keyring.backends.SecretService'):
            raise RuntimeError('secure OS credential store unavailable; configure an OS-backed keyring')
        self.keyring=keyring

    def get(self,key): return self.keyring.get_password('tester-storage-v1',key)
    def put(self,key,value): self.keyring.set_password('tester-storage-v1',key,value)


def installation_status():
    return {name:shutil.which(name) for name in ('psql','pg_dump','pg_restore','pg_isready')}


def _name(name):
    if not isinstance(name,str) or not re.fullmatch(r'(tester|bybit)_[a-z0-9_]{1,48}',name):
        raise ValueError('use a dedicated tester_/bybit_ lowercase identifier (max 55 chars)')
    return name


def bootstrap(admin_dsn,database='tester_history',owner_role='tester_history_owner',runtime_role='tester_history_runtime',secret_store=None):
    """Create only tagged dedicated objects. Never reset existing passwords or adopt foreign roles.

    admin_dsn is provided through environment/service or a programmatic secure prompt, never CLI argv.
    Owner is NOLOGIN; administrator SET ROLEs for migrations. Runtime has no DDL/role capabilities.
    """
    for n in (database,owner_role,runtime_role): _name(n)
    if len({database,owner_role,runtime_role})!=3: raise ValueError('distinct database and role names required')
    secret_store=secret_store if secret_store is not None else SecretStore()
    marker='tester-storage:v1:'+database
    with psycopg.connect(admin_dsn,autocommit=True,connect_timeout=8) as admin:
        admin.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))",(marker,))
        try:
            endpoint=admin.info.host+':'+str(admin.info.port)
            secret_key=digest([endpoint,database,runtime_role])
            roles={}
            for name,kind in ((owner_role,'owner'),(runtime_role,'runtime')):
                role=admin.execute('''SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
                    shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s''',(name,)).fetchone()
                if role and (role[-1]!=marker+':'+kind or any(role[:5])): raise ValueError('existing role is not a minimally privileged managed role')
                if role and admin.execute('''SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s)''',(name,)).fetchone()[0]:
                    raise ValueError('managed role has unexpected role membership')
                roles[name]=role
            db=admin.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",(database,)).fetchone()
            if db and db!=(owner_role,marker): raise ValueError('existing database is not owned/tagged by this bootstrap')
            password=secret_store.get(secret_key)
            if roles[runtime_role] and password is None: raise ValueError('existing runtime secret is unavailable; refusing password reset')
            if password is None:
                password=secrets.token_urlsafe(48)
                secret_store.put(secret_key,password)
            # Never enable SQL statement logging around password DDL, including error logging.
            admin.execute("SET log_statement='none'")
            admin.execute("SET log_min_error_statement='panic'")
            admin.execute("SET log_min_duration_statement=-1")
            admin.execute("SET log_min_duration_sample=-1")
            admin.execute("SET log_duration=off")
            if admin.execute("SELECT current_setting('pgaudit.log',true)").fetchone()[0] not in (None,'','none'):
                raise ValueError('role provisioning requires an administrator session without statement audit logging')
            for name,kind in ((owner_role,'owner'),(runtime_role,'runtime')):
                if not roles[name]:
                    with admin.transaction():
                        if kind=='owner': admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(name)))
                        else: admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(name),sql.Literal(password)))
                        admin.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(name),sql.Literal(marker+':'+kind)))
            if not db:
                admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(database),sql.Identifier(owner_role)))
                admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(database),sql.Literal(marker)))
            admin.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
            admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(runtime_role)))
            target=psycopg.conninfo.make_conninfo(admin_dsn,dbname=database)
            with psycopg.connect(target,connect_timeout=8) as c:
                c.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
                c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(owner_role)))
                migrate(c)
                c.execute(sql.SQL('GRANT USAGE ON SCHEMA tester_storage TO {}').format(sql.Identifier(runtime_role)))
                c.execute(sql.SQL('GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA tester_storage TO {}').format(sql.Identifier(runtime_role)))
                c.execute(sql.SQL('REVOKE INSERT ON tester_storage.schema_migrations FROM {}').format(sql.Identifier(runtime_role)))
                c.execute(sql.SQL('GRANT UPDATE ON tester_storage.download_jobs,tester_storage.ingestion_checkpoints TO {}').format(sql.Identifier(runtime_role)))
            return dict(database=database,owner_role=owner_role,runtime_role=runtime_role,runtime_secret_key=secret_key)
        finally:
            admin.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(marker,))
