#!/usr/bin/env python3
"""
Usage:
    server.py [--nodb | --db TYPE]

Options:
    --nodb      Don't use a database (Use a mock.Mock). Caution: Will break things.
    --db TYPE   Use TYPE database driver [default: QMYSQL]
"""

import asyncio

if __name__ == '__main__':
    app = QtCore.QCoreApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)


import sys
import logging
from logging import handlers
import signal
import socket

from quamash import QEventLoop
from PySide import QtSql, QtCore
from PySide.QtCore import QTimer

from passwords import DB_SERVER, DB_PORT, DB_LOGIN, DB_PASSWORD, DB_TABLE
from server.db import ContextCursor
from server.gameservice import GameService
from server.playerservice import PlayerService
import config
import server

if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    try:
        def signal_handler(signal, frame):
            logger.info("Received signal, shutting down")
            if not done.done():
                done.set_result(0)

        if config.LIBRARY_PATH:
            app.addLibraryPath(config.LIBRARY_PATH)

        done = asyncio.Future()

        from docopt import docopt
        args = docopt(__doc__, version='FAF Server')

        rootlogger = logging.getLogger("")
        logHandler = handlers.RotatingFileHandler(config.LOG_PATH + "server.log", backupCount=1024, maxBytes=16777216)
        logFormatter = logging.Formatter('%(asctime)s %(levelname)-8s %(name)-20s %(message)s')
        logHandler.setFormatter(logFormatter)
        rootlogger.addHandler(logHandler)
        rootlogger.setLevel(config.LOG_LEVEL)

        if args['--nodb']:
            from unittest import mock
            db = mock.Mock()
        else:
            db = QtSql.QSqlDatabase(args['--db'])
            db.setHostName(DB_SERVER)
            db.setPort(DB_PORT)

            db.setDatabaseName(DB_TABLE)
            db.setUserName(DB_LOGIN)
            db.setPassword(DB_PASSWORD)
            db.setConnectOptions("MYSQL_OPT_RECONNECT=1")

        if not db.open():
            logger.error(db.lastError().text())
            sys.exit(1)

        # Make sure we can shutdown gracefully
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        def poll_signal():
            pass
        timer = QTimer()
        timer.timeout.connect(poll_signal)
        timer.start(200)

        pool_fut = asyncio.async(server.db.connect(host=DB_SERVER,
                                                   port=DB_PORT,
                                                   user=DB_LOGIN,
                                                   password=DB_PASSWORD,
                                                   maxsize=10,
                                                   db=DB_TABLE,
                                                   loop=loop))
        db_pool = loop.run_until_complete(pool_fut)

        player_service = PlayerService.initialise_player_service()
        game_service = GameService.initialise_game_service(player_service, db)

        ctrl_server = loop.run_until_complete(server.run_control_server(loop))

        lobby_server = loop.run_until_complete(
            server.run_lobby_server(('', 8001),
                                    db,
                                    loop)
        )
        for sock in lobby_server.sockets:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        nat_packet_server, game_server = \
            server.run_game_server(('', 8000),
                                   loop)
        game_server = loop.run_until_complete(game_server)

        loop.run_until_complete(done)
        loop.close()

    except Exception as ex:
        logger.exception("Failure booting server {}".format(ex))
