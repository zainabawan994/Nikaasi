import os

# Keep tests offline and deterministic even when nikaasi/.env holds real values:
# an empty value also stops the .env loader from filling these in.
os.environ["GEMINI_API_KEY"] = ""
os.environ["DATABASE_URL"] = ""
