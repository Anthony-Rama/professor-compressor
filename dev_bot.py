"""Start the isolated development bot; never load the production .env."""

from professor_compressor.development import main

if __name__ == "__main__":
    raise SystemExit(main())
