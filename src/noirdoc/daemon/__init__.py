"""Long-lived daemon that holds detection models in memory.

The CLI's ``redact`` command transparently spawns and talks to this daemon
over a Unix domain socket, so model loading (~10 s on cold start) is paid
once per session instead of once per invocation.
"""
