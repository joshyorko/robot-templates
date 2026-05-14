import os
from pathlib import Path

from robocorp.tasks import task

from RPA.Assistant.types import WindowLocation, Size
import RPA.Assistant
from openai import OpenAI

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DEFAULT_MODEL = "gpt-4.1"


assistant = RPA.Assistant.Assistant()
gpt_conversation_display = []
gpt_conversation_internal = []
gpt_model = DEFAULT_MODEL
openai_client = None


@task
def run_assistant():
    authorize_openai()

    display_conversation()

    assistant.run_dialog(
        timeout=1800,
        title="AI Chat",
        on_top=True,
        location=WindowLocation.Center,
    )


def authorize_openai():
    global gpt_model, openai_client
    load_dotenv(ENV_PATH)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Export it locally or create "
            "04-python-assistant-ai/.env; do not commit secrets."
        )

    gpt_model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    print(f"OpenAI credentials found; starting local assistant with {gpt_model}.")
    openai_client = OpenAI(api_key=openai_key)


def show_spinner():
    assistant.clear_dialog()
    assistant.add_loading_spinner(
        name="spinner", width=60, height=60, stroke_width=8
    )
    assistant.refresh_dialog()


def ask_gpt(form_data: dict):
    global gpt_conversation_internal

    show_spinner()

    gpt_conversation_internal.append(
        {"role": "user", "content": form_data["input"]}
    )
    response = openai_client.chat.completions.create(
        model=gpt_model,
        messages=gpt_conversation_internal,
        temperature=1,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
    )
    text = response.choices[0].message.content
    gpt_conversation_internal.append({"role": "assistant", "content": text})
    gpt_conversation_display.append((form_data["input"], text))

    display_conversation()
    assistant.refresh_dialog()


def display_conversation():
    assistant.clear_dialog()
    assistant.add_heading("Conversation")
    for reply in gpt_conversation_display:
        assistant.add_text("You:", size=Size.Small)
        assistant.open_container(background_color="#C091EF", margin=2)
        assistant.add_text(reply[0])
        assistant.close_container()

        assistant.add_text("GPT:", size=Size.Small)
        assistant.open_container(background_color="#A5AACD", margin=2)
        assistant.add_text(reply[1])
        assistant.close_container()

    display_buttons()


def display_buttons():
    assistant.add_text_input(
        "input", placeholder="Send a message", minimum_rows=3
    )
    assistant.add_next_ui_button("Send", ask_gpt)
    assistant.add_submit_buttons("Close", default="Close")
