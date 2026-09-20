"""MCP Server entry point using official MCP SDK.

This module implements the MCP server using the official Python MCP SDK
with stdio transport. It ensures stdout only contains protocol messages
while all logs go to stderr.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from src.mcp_server.protocol_handler import create_mcp_server
from src.observability.logger import get_logger

if TYPE_CHECKING:
    pass


SERVER_NAME = "modular-rag-mcp-server"
SERVER_VERSION = "0.1.0"

# ``scripts/evaluate.py`` loads the repository .env explicitly.  The MCP
# server is launched directly as a subprocess, so it must do the same before
# any provider is initialized by a tool call.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class _RawStdioReader:
    """Async line reader backed by a raw stdio file descriptor.

    The MCP SDK's default ``anyio.wrap_file(TextIOWrapper)`` path does not
    consume piped stdin reliably in this Python 3.12 subprocess environment.
    Reading the descriptor directly keeps the transport Stdio while avoiding
    the buffered text-wrapper interaction.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buffer = b""

    def __aiter__(self) -> "_RawStdioReader":
        return self

    async def __anext__(self) -> str:
        line = await self.readline()
        if not line:
            raise StopAsyncIteration
        return line

    async def readline(self) -> str:
        while b"\n" not in self._buffer:
            chunk = await self._read_chunk()
            if not chunk:
                break
            self._buffer += chunk

        if b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            return (line + b"\n").decode("utf-8", errors="replace")

        line, self._buffer = self._buffer, b""
        return line.decode("utf-8", errors="replace")

    async def _read_chunk(self) -> bytes:
        """Wait for fd readability without a blocking worker-thread read."""
        loop = asyncio.get_running_loop()
        ready = loop.create_future()

        def _on_readable() -> None:
            if ready.done():
                return
            try:
                ready.set_result(os.read(self._fd, 65536))
            except BaseException as exc:
                ready.set_exception(exc)

        try:
            loop.add_reader(self._fd, _on_readable)
        except (NotImplementedError, RuntimeError):
            # POSIX/WSL uses add_reader.  Keep a fallback for event loops that
            # do not expose fd watchers (for example, some Windows loops).
            return await asyncio.to_thread(os.read, self._fd, 65536)

        try:
            return await ready
        finally:
            loop.remove_reader(self._fd)

    async def aclose(self) -> None:
        return None


class _RawStdioWriter:
    """Async text writer backed by a raw stdio file descriptor."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    async def write(self, data: str) -> int:
        pending = data.encode("utf-8")
        total = len(pending)
        while pending:
            written = os.write(self._fd, pending)
            pending = pending[written:]
        return total

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


@contextmanager
def _isolated_stdio_streams() -> Iterator[tuple[_RawStdioReader, _RawStdioWriter]]:
    """Expose raw Stdio streams and divert accidental fd-level stdout writes.

    The MCP wire keeps the original stdout descriptor.  fd 1 is temporarily
    pointed at stderr so ordinary ``print`` calls cannot corrupt that wire.
    """
    wire_stdin_fd = os.dup(0)
    wire_stdout_fd = os.dup(1)
    diverted_stdout_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_RDONLY)

    try:
        os.dup2(devnull_fd, 0)
        os.dup2(diverted_stdout_fd, 1)
        yield _RawStdioReader(wire_stdin_fd), _RawStdioWriter(wire_stdout_fd)
    finally:
        # Flush while fd 1 still points at stderr; buffered accidental output
        # must never be released onto the MCP wire after restoration.
        try:
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        os.dup2(wire_stdin_fd, 0)
        os.dup2(wire_stdout_fd, 1)
        for fd in (wire_stdin_fd, wire_stdout_fd, diverted_stdout_fd, devnull_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def _redirect_all_loggers_to_stderr() -> None:
    """Redirect all root logger handlers to stderr.

    MCP stdio transport reserves stdout for JSON-RPC messages.
    Any logging to stdout corrupts the protocol stream.
    """
    import logging as _logging

    root = _logging.getLogger()
    stderr_handler = _logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        _logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    # Replace any existing stream handlers that might point to stdout
    for handler in root.handlers[:]:
        if isinstance(handler, _logging.StreamHandler) and not isinstance(
            handler, _logging.FileHandler
        ):
            root.removeHandler(handler)
    root.addHandler(stderr_handler)


def _preload_heavy_imports() -> None:
    """Eagerly import heavy third-party modules in the **main thread**.

    MCP SDK uses anyio + background threads for stdin/stdout I/O.
    When a tool handler runs ``asyncio.to_thread(fn)``, *fn* executes in
    a new worker thread.  If it tries to ``import chromadb`` (which
    transitively pulls in onnxruntime, numpy, sqlite3 C extensions …),
    that import can deadlock with the stdin-reader thread because both
    compete for Python's global *import lock*.

    Pre-importing here – before anyio spins up its I/O threads – avoids
    the deadlock entirely: subsequent ``import`` statements in worker
    threads simply hit ``sys.modules`` and return immediately.
    """
    # chromadb is the heaviest culprit (onnxruntime, numpy, …)
    try:
        import chromadb  # noqa: F401
        import chromadb.config  # noqa: F401
    except ImportError:
        pass  # optional at install time

    # Internal modules that tools lazy-import inside asyncio.to_thread
    try:
        import src.core.query_engine.query_processor  # noqa: F401
        import src.core.query_engine.hybrid_search  # noqa: F401
        import src.core.query_engine.dense_retriever  # noqa: F401
        import src.core.query_engine.sparse_retriever  # noqa: F401
        import src.core.query_engine.reranker  # noqa: F401
        import src.ingestion.storage.bm25_indexer  # noqa: F401
        import src.libs.embedding.embedding_factory  # noqa: F401
        import src.libs.vector_store.vector_store_factory  # noqa: F401
    except ImportError:
        pass


async def run_stdio_server_async() -> int:
    """Run MCP server over stdio asynchronously.

    Returns:
        Exit code.
    """
    # Import here to avoid import errors if mcp not installed
    import mcp.server.stdio

    # Ensure ALL logging goes to stderr (stdout is reserved for JSON-RPC)
    _redirect_all_loggers_to_stderr()

    # Pre-load heavy deps in main thread to prevent import-lock deadlocks
    # when tool handlers later call asyncio.to_thread().
    _preload_heavy_imports()

    logger = get_logger(log_level="INFO")
    logger.info("Starting MCP server (stdio transport) with official SDK.")

    # Create server with protocol handler
    server = create_mcp_server(SERVER_NAME, SERVER_VERSION)

    # Run with stdio transport
    with _isolated_stdio_streams() as (stdin_stream, stdout_stream):
        async with mcp.server.stdio.stdio_server(
            stdin=stdin_stream,
            stdout=stdout_stream,
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    logger.info("MCP server shutting down.")
    return 0


def run_stdio_server() -> int:
    """Run MCP server over stdio (synchronous wrapper).

    Returns:
        Exit code.
    """
    return asyncio.run(run_stdio_server_async())


def main() -> int:
    """Entry point for stdio MCP server."""
    return run_stdio_server()


if __name__ == "__main__":
    sys.exit(main())
