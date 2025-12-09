import logging
from robocorp.tasks import task
from openai import OpenAI
from dotenv import load_dotenv
import os

# Debug: Check if .env file exists and load it
env_path = '/var/home/kdlocpanda/second_brain/Areas/RPA/robots/test-assistant/.env'
print(f"Looking for .env file at: {env_path}")
print(f".env file exists: {os.path.exists(env_path)}")

load_dotenv_result = load_dotenv(env_path)
print(f"load_dotenv() returned: {load_dotenv_result}")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@task
def test_keys():
    # Debug: Check what we got from environment
    api_key = os.environ.get("OPENAI_API_KEY")
    print(f"OPENAI_API_KEY length: {len(api_key) if api_key else 0}")
    print(f"OPENAI_API_KEY first 20 chars: {api_key[:20] if api_key else 'None'}")
    print(f"OPENAI_API_KEY last 20 chars: {api_key[-20:] if api_key else 'None'}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Script directory: {os.path.dirname(__file__)}")
    
    # Compare with direct file read
    try:
        with open('.env', 'r') as f:
            content = f.read()
            print(f".env file content length: {len(content)}")
            for line in content.strip().split('\n'):
                if line.startswith('OPENAI_API_KEY='):
                    file_key = line.split('=', 1)[1]
                    print(f"Key from file length: {len(file_key)}")
                    print(f"Key from file first 20: {file_key[:20]}")
                    print(f"Key from file last 20: {file_key[-20:]}")
                    print(f"Keys match: {api_key == file_key}")
    except Exception as e:
        print(f"Error reading .env file: {e}")
    
    client = OpenAI(api_key=api_key)

    response = client.responses.create(
        model="gpt-5",
        input="Write a one-sentence bedtime story about a unicorn."
    )

    logger.info(response.output_text)
