"""临时的 TCP→unix socket 代理：让 Windows 上的 Testcontainers 通过 tcp://127.0.0.1:2375 访问 WSL 里的 Docker。
只监听 WSL 的回环地址；本会话结束后手动停止，不改动 dockerd 配置。"""

import contextlib
import socket
import threading

SRC = "/var/run/docker.sock"


def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)


def handle(c: socket.socket) -> None:
    u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    u.connect(SRC)
    threading.Thread(target=pipe, args=(c, u), daemon=True).start()
    threading.Thread(target=pipe, args=(u, c), daemon=True).start()


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 2375))
srv.listen(64)
print("docker proxy on 127.0.0.1:2375", flush=True)
while True:
    conn, _ = srv.accept()
    handle(conn)
