"""Builtin skill: summarize text into key bullet points."""

SKILL_NAME = "summarize"
SKILL_DESCRIPTION = "Summarize a given text into key bullet points. Returns a prompt for the agent to perform the summarization."
SKILL_VERSION = "1.0.0"
SKILL_PARAMETERS = {"text": str, "max_points": int}


async def execute(args):
    text = args["text"]
    max_points = args.get("max_points", 5) or 5
    return {
        "content": [{
            "type": "text",
            "text": (
                f"Please summarize the following text into at most {max_points} concise bullet points. "
                f"Focus on the key ideas and important details:\n\n{text}"
            ),
        }],
    }
