"""Builtin skill: translate text between languages."""

SKILL_NAME = "translate"
SKILL_DESCRIPTION = "Translate text between languages. Returns a prompt for the agent to perform the translation."
SKILL_VERSION = "1.0.0"
SKILL_PARAMETERS = {"text": str, "target_language": str, "source_language": str}


async def execute(args):
    text = args["text"]
    target = args["target_language"]
    source = args.get("source_language", "auto-detect") or "auto-detect"
    return {
        "content": [{
            "type": "text",
            "text": (
                f"Please translate the following text from {source} to {target}. "
                f"Preserve the original tone and meaning:\n\n{text}"
            ),
        }],
    }
