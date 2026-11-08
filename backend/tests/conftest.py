import os

# Keep tests offline and deterministic even when nikaasi/.env holds a real key.
os.environ["GEMINI_API_KEY"] = ""
