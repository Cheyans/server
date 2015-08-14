"""
This module is the 'top level' configuration for all the unit tests.

'Real world' fixtures are put here.
If a test suite needs specific mocked versions of dependencies,
these should be put in the ``conftest.py'' relative to it.
"""

import asyncio

import logging
import sys

pool = None
def make_db_pool(request, loop):
    def opt(val):
        return request.config.getoption(val)
    host, user, pw, db = opt('--mysql_host'), opt('--mysql_username'), opt('--mysql_password'), opt('--mysql_database')
    pool_fut = asyncio.async(server.db.connect(loop=loop,
                                               host=host,
                                               user=user,
                                               password=pw,
                                               db=db))
    pool = loop.run_until_complete(pool_fut)

    @asyncio.coroutine
    def setup():
        with (yield from pool) as conn:
            cur = yield from conn.cursor()
            with open('db-structure.sql', 'r', encoding='utf-8') as data:
                yield from cur.execute('DROP DATABASE IF EXISTS `%s`;' % db)
                yield from cur.execute('CREATE DATABASE IF NOT EXISTS `%s`;' % db)
                yield from cur.execute("USE `%s`;" % db)
                yield from cur.execute(data.read())
            with open('tests/data/db-fixtures.sql', 'r', encoding='utf-8') as data:
                yield from cur.execute(data.read())
                yield from cur.close()

    loop.run_until_complete(setup())

    return pool

@pytest.fixture
def db_pool(request, loop):
    def fin():
        pool.close()
        loop.run_until_complete(pool.wait_closed())

    request.addfinalizer(fin)
    pool = make_db_pool(request, loop)

    return db_pool

asyncio.get_event_loop().run_until_complete(asyncio.async(make_db_pool()))

import server

from server.abc.base_game import InitMode
from server.players import PlayerState
from server.players import Player
from server.playerservice import PlayerService
from server.gameservice import GameService
from server.games import Game

from server.qt_compat import QtCore, QtSql, QtNetwork

import aiomysql
import pytest
from unittest import mock
from trueskill import Rating


logging.getLogger().setLevel(logging.DEBUG)

import os
os.environ['QUAMASH_QTIMPL'] = 'PySide'

import quamash

def async_test(f):
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)
    return wrapper

def pytest_pycollect_makeitem(collector, name, obj):
    if name.startswith('test_') and asyncio.iscoroutinefunction(obj):
        return list(collector._genfunctions(name, obj))

def pytest_addoption(parser):
    parser.addoption('--slow', action='store_true', default=False,
                     help='Also run slow tests')
    parser.addoption('--aiodebug', action='store_true', default=False,
                     help='Enable asyncio debugging')

def pytest_configure(config):
    if config.getoption('--aiodebug'):
        logging.getLogger('quamash').setLevel(logging.DEBUG)
        logging.captureWarnings(True)
    else:
        logging.getLogger('quamash').setLevel(logging.INFO)


def pytest_runtest_setup(item):
    """
    Skip tests if they are marked slow, and --slow isn't given on the commandline
    :param item:
    :return:
    """
    if getattr(item.obj, 'slow', None) and not item.config.getvalue('slow'):
        pytest.skip("slow test")

def pytest_pyfunc_call(pyfuncitem):
    testfn = pyfuncitem.obj

    if not asyncio.iscoroutinefunction(testfn):
        return

    funcargs = pyfuncitem.funcargs
    testargs = {}
    for arg in pyfuncitem._fixtureinfo.argnames:
        testargs[arg] = funcargs[arg]
    loop = testargs.get('loop', asyncio.get_event_loop())
    coro = asyncio.wait_for(testfn(**testargs), 5)

    try:
        loop.run_until_complete(coro)
    except RuntimeError as err:
        logging.error(err)
        raise err
    return True

@pytest.fixture(scope='session')
def application():
    return QtCore.QCoreApplication([])

@pytest.fixture(scope='function')
def loop(request, application):
    loop = quamash.QEventLoop(application)
    loop.set_debug(True)
    asyncio.set_event_loop(loop)
    additional_exceptions = []

    def finalize():
        sys.excepthook = orig_excepthook
        try:
            loop.close()
        except KeyError:
            pass
        finally:
            asyncio.set_event_loop(None)
            for exc in additional_exceptions:
                if (
                        os.name == 'nt' and
                        isinstance(exc['exception'], WindowsError) and
                        exc['exception'].winerror == 6
                ):
                    # ignore Invalid Handle Errors
                    continue
                raise exc['exception']
    def except_handler(loop, ctx):
        additional_exceptions.append(ctx)
    def excepthook(type, *args):
        loop.stop()
    orig_excepthook = sys.excepthook
    sys.excepthook = excepthook
    loop.set_exception_handler(except_handler)
    request.addfinalizer(finalize)
    return loop

@pytest.fixture
def sqlquery():
    query = mock.MagicMock()
    query.exec_ = lambda: 0
    query.size = lambda: 0
    query.lastInsertId = lambda: 1
    query.prepare = mock.MagicMock()
    query.addBindValue = lambda v: None
    return query

@pytest.fixture
def db(sqlquery):
    # Since PySide does strict type checking, we cannot mock this directly
    db = QtSql.QSqlDatabase()
    db.exec_ = lambda q: sqlquery
    db.isOpen = mock.Mock(return_value=True)
    return db

@pytest.fixture
def mock_db_pool(loop):
    return mock.create_autospec(aiomysql.Pool(0, 10, False, loop))

@pytest.fixture
def connected_game_socket():
    game_socket = mock.Mock(spec=QtNetwork.QTcpSocket)
    game_socket.state = mock.Mock(return_value=QtNetwork.QTcpSocket.ConnectedState)
    game_socket.isValid = mock.Mock(return_value=True)
    return game_socket

@pytest.fixture
def transport():
    return mock.Mock(spec=asyncio.Transport)

@pytest.fixture
def game(players, db):
    mock_parent = mock.Mock()
    mock_parent.db = db
    game = mock.create_autospec(spec=Game(1, mock_parent))
    players.hosting.getGame = mock.Mock(return_value=game)
    players.joining.getGame = mock.Mock(return_value=game)
    players.peer.getGame = mock.Mock(return_value=game)
    game.hostPlayer = players.hosting
    game.init_mode = InitMode.NORMAL_LOBBY
    game.name = "Some game name"
    game.id = 1
    return game

@pytest.fixture
def create_player():
    def make(login='', id=0, port=6112, state=PlayerState.HOSTING, ip='127.0.0.1', global_rating=Rating(1500, 250), ladder_rating=Rating(1500, 250)):
        p = mock.create_autospec(spec=Player(login))
        p.global_rating = global_rating
        p.ladder_rating = ladder_rating
        p.getLogin = mock.Mock(return_value=login)
        p.getId = mock.Mock(return_value=id)
        p.getIp = mock.Mock(return_value=ip)
        p.ip = ip
        p.game_port = port
        p.state = state
        p.id = id
        p.login = login
        p.address_and_port = "{}:{}".format(ip, port)
        return p
    return make

@pytest.fixture
def players(create_player):
    return mock.Mock(
        hosting=create_player(login='Paula_Bean', id=1, port=6112, state=PlayerState.HOSTING),
        peer=create_player(login='That_Guy', id=2, port=6112, state=PlayerState.JOINING),
        joining=create_player(login='James_Kirk', id=3, port=6112, state=PlayerState.JOINING)
    )

@pytest.fixture
def player_service(players, mock_db_pool):
    p = mock.Mock(spec=PlayerService(mock_db_pool))
    p.find_by_ip_and_session = mock.Mock(return_value=players.hosting)
    return p

@pytest.fixture
def games(game, players, db):
    service = mock.create_autospec(GameService(players, db))
    service.find_by_id.return_value = game
    return service
