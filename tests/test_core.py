import unittest
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import select

import sqlalchemy_opentracing
from .dummies import *

class TestSQLAlchemyCore(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.users_table = Table('users', MetaData(),
            Column('id', Integer, primary_key=True),
            Column('name', String),
        )
        sqlalchemy_opentracing.register_connectable(self.engine)

    def test_traced(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        sqlalchemy_opentracing.init_tracing(tracer)
        sqlalchemy_opentracing.set_traced(creat)
        self.engine.execute(creat)

        self.assertEqual(1, len(tracer.spans))
        self.assertEqual(tracer.spans[0].operation_name, 'create_table')
        self.assertEqual(tracer.spans[0].is_finished, True)
        self.assertEqual(tracer.spans[0].tags, {
            'component': 'sqlalchemy',
            'db.statement': 'CREATE TABLE users (id INTEGER NOT NULL, name VARCHAR, PRIMARY KEY (id))',
            'db.type': 'sql',
        })

    def test_traced_none(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        sqlalchemy_opentracing.init_tracing(tracer)
        self.engine.execute(creat)

        self.assertEqual(0, len(tracer.spans))

    def test_traced_all(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        sqlalchemy_opentracing.init_tracing(tracer, trace_all=True)
        self.engine.execute(creat)

        self.assertEqual(1, len(tracer.spans))

    def test_traced_error(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        sqlalchemy_opentracing.init_tracing(tracer)
        self.engine.execute(creat)
        self.assertEqual(0, len(tracer.spans))

        sqlalchemy_opentracing.set_traced(creat)
        try:
            self.engine.execute(creat)
        except OperationalError:
            pass # Do nothing - it's responsibility of OT to finish tracing it.

        self.assertEqual(1, len(tracer.spans))
        self.assertEqual(tracer.spans[0].is_finished, True)
        self.assertEqual(tracer.spans[0].tags, {
            'component': 'sqlalchemy',
            'db.statement': 'CREATE TABLE users (id INTEGER NOT NULL, name VARCHAR, PRIMARY KEY (id))',
            'db.type': 'sql',
            'error': 'true',
        })

    def test_traced_transaction(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)
        ins = self.users_table.insert().values(name='John Doe')
        sel = select([self.users_table])

        sqlalchemy_opentracing.init_tracing(tracer)
        parent_span = DummySpan('parent span')
        conn = self.engine.connect()
        with conn.begin() as trans:
            sqlalchemy_opentracing.set_parent_span(conn, parent_span)
            conn.execute(creat)
            conn.execute(ins)
            conn.execute(sel)

        self.assertEqual(3, len(tracer.spans))
        self.assertEqual(True, all(map(lambda x: x.is_finished, tracer.spans)))
        self.assertEqual(True, all(map(lambda x: x.child_of == parent_span, tracer.spans)))
        self.assertEqual(['create_table', 'insert', 'select'],
                         map(lambda x: x.operation_name, tracer.spans))

    def test_traced_rollback(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)
        ins = self.users_table.insert().values(name='John Doe')

        # Don't trace this.
        self.engine.execute(creat)

        sqlalchemy_opentracing.init_tracing(tracer)
        parent_span = DummySpan('parent span')
        conn = self.engine.connect()
        try:
            with conn.begin() as tx:
                sqlalchemy_opentracing.set_parent_span(conn, parent_span)
                conn.execute(ins)
                conn.execute(creat)
        except OperationalError:
            pass

        self.assertEqual(2, len(tracer.spans))
        self.assertEqual(True, all(map(lambda x: x.is_finished, tracer.spans)))
        self.assertEqual(True, all(map(lambda x: x.child_of == parent_span, tracer.spans)))
        self.assertEqual(['insert', 'create_table'],
                         map(lambda x: x.operation_name, tracer.spans))
        self.assertEqual(['false', 'true'],
                         map(lambda x: x.tags.get('error', 'false'), tracer.spans))

    def test_traced_after_transaction(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        sqlalchemy_opentracing.init_tracing(tracer)
        conn = self.engine.connect()
        with conn.begin() as tx:
            sqlalchemy_opentracing.set_traced(conn)
            conn.execute(creat)

        self.assertEqual(1, len(tracer.spans))

        # Do something right after with this connection,
        # no tracing should happen.
        tracer.clear()
        ins = self.users_table.insert().values(name='John Doe')
        with conn.begin() as tx:
            conn.execute(ins)

        self.assertEqual(0, len(tracer.spans))

    def test_traced_after_rollback(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        # Create a table, but don't trace it
        sqlalchemy_opentracing.init_tracing(tracer)
        conn = self.engine.connect()
        with conn.begin() as tx:
            conn.execute(creat)

        try:
            with conn.begin() as tx:
                sqlalchemy_opentracing.set_traced(conn)
                conn.execute(creat)
        except OperationalError:
            pass

        self.assertEqual(1, len(tracer.spans))

        # Do something right after with this connection,
        # no tracing should happen.
        tracer.clear()
        ins = self.users_table.insert().values(name='John Doe')
        with conn.begin() as tx:
            conn.execute(ins)

        self.assertEqual(0, len(tracer.spans))

    def test_unregister_connectable(self):
        tracer = DummyTracer()
        creat = CreateTable(self.users_table)

        sqlalchemy_opentracing.init_tracing(tracer, trace_all=True)
        self.engine.execute(creat)
        self.assertEqual(1, len(tracer.spans))

        tracer.clear()
        sqlalchemy_opentracing.unregister_connectable(self.engine)

        # Further events should cause no spans at all.
        sel = select([self.users_table])
        sqlalchemy_opentracing.set_traced(sel)
        self.engine.execute(sel)
        self.assertEqual(0, len(tracer.spans))
