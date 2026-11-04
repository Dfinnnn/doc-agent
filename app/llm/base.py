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

    def chat_tools(self, messages, tools):
        """Send the conversation so far plus the tools the model may call.

        Returns a dict:
            content     - the model's text answer (None if it only calls tools)
            tool_calls  - [{"id", "name", "arguments": dict}], empty if none
            message     - the reply in the provider's format, to append to messages
        """
        raise NotImplementedError
