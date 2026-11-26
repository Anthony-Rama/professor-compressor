import os

# Tests never read a developer's real .env file, whichever module runs first.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
