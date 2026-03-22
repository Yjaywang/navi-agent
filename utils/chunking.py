def chunk_text(text: str, max_len: int = 1950) -> list[str]:
    """Split text into chunks that fit within Discord's 2000-char message limit.

    Tries to split on newlines first, then on spaces, then hard-cuts as a last resort.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Try to find a newline to split on
        split_pos = remaining.rfind("\n", 0, max_len)

        # If no newline, try a space
        if split_pos == -1:
            split_pos = remaining.rfind(" ", 0, max_len)

        # If no space either, hard cut
        if split_pos == -1:
            split_pos = max_len

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    return chunks
