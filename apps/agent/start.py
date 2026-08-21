"""Maritime custom-framework entry point for the LangChain agent."""

import os

import uvicorn

from maritime_ratify.agent_runtime import AgentSettings, create_agent_app


def build_app():
    return create_agent_app(AgentSettings.from_environment())


if __name__ == "__main__":
    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
