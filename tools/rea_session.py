"""Persistent REA MCP session so many Ghidra queries share one import.

  python3 rea_session.py serve TARGET.elf          open TARGET once and serve queries (run in background)
  python3 rea_session.py q TOOL JSON [TOOL JSON]   send tool calls to the running server
  python3 rea_session.py tools                     list MCP tool names

Set REA_SOCK to run several sessions (e.g. one per RAM slice) side by side.
"""
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REA = os.path.join(HERE, "rea-ps2")
WORK = os.path.join(HERE, "..", "work")
SOCK = os.environ.get("REA_SOCK", os.path.join(WORK, "rea-session.sock"))


class ReaSession:
    def __init__(self):
        self.proc = subprocess.Popen([REA, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(os.path.join(WORK, "rea-mcp.log"), "ab"),
                                     text=True, bufsize=1)
        self.next_id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "sotc-mp", "version": "0"}})
        self.notify("notifications/initialized", {})

    def notify(self, method, params):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")

    def request(self, method, params):
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id,
                                          "method": method, "params": params}) + "\n")
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("REA MCP server exited")
            msg = json.loads(line)
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def tools(self):
        return [t["name"] for t in self.request("tools/list", {})["tools"]]

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if "structuredContent" in result:
            return result["structuredContent"]
        return "\n".join(c.get("text", "") for c in result.get("content", []))


def serve(target):
    session = ReaSession()
    print(session.call("open_binary", {"path": os.path.abspath(target), "provider_id": "ghidra"}), flush=True)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCK)
    server.listen(1)
    print("serving", target, flush=True)
    while True:
        conn, _ = server.accept()
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            name, arguments = json.loads(data)
            try:
                out = session.call(name, arguments)
            except Exception as exc:  # report and keep serving
                out = "ERROR: %s" % exc
            conn.sendall(json.dumps(out).encode())


def query(name, arguments):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(SOCK)
    conn.sendall((json.dumps([name, arguments]) + "\n").encode())
    data = b""
    while True:
        chunk = conn.recv(1 << 20)
        if not chunk:
            break
        data += chunk
    return json.loads(data)


def main():
    cmd = sys.argv[1]
    if cmd == "tools":
        print("\n".join(ReaSession().tools()))
    elif cmd == "serve":
        serve(sys.argv[2])
    elif cmd == "q":
        args = sys.argv[2:]
        for name, raw in zip(args[0::2], args[1::2]):
            out = query(name, json.loads(raw))
            print("=== %s %s" % (name, raw))
            if isinstance(out, dict) and "normalized_result" in out:
                out = out["normalized_result"]
            print(out if isinstance(out, str) else json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
