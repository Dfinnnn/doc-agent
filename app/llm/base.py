"""The interface every LLM adapter follows, so the rest of the app never
depends on a specific provider."""


class LLM:
    """What an LLM adapter must be able to do."""

    def chat_json(self, system, user):
        """Send instructions (system) and a message (user); return the reply as a dict.

        The model is asked to answer in JSON. Checking that the JSON has the
        right fields is the caller's job, not the adapter's.
        """
        raise NotImplementedError
