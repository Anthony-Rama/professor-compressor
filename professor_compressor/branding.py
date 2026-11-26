"""Public identity of the production Discord application and its listings."""

APPLICATION_ID = "1550752400271351839"
PROJECT_URL = "https://github.com/Anthony-Rama/professor-compressor"
BOT_INVITE_URL = (
    f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}"
    "&permissions=277025426432&integration_type=0&scope=bot+applications.commands"
)
DSC_LISTING_URL = f"https://dsc.sh/bot/{APPLICATION_ID}"
DISCORD_BOT_LIST_URL = "https://discordbotlist.com/bots/professor-compressor"
DISCORD_BOTS_URL = f"https://discord.bots.gg/bots/{APPLICATION_ID}"
TOPGG_LISTING_URL = f"https://top.gg/bot/{APPLICATION_ID}"
# Identifies outbound requests to third-party listing and webhook services.
USER_AGENT = f"ProfessorCompressor/1.0 (+{PROJECT_URL})"
