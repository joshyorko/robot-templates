# Python AI Chat Assistant

A chat interface that allows you to communicate with OpenAI's GPT models using an RPA Assistant GUI.

## Setup

1. **Install RCC** (if not already installed):
   - Download from [RCC releases](https://github.com/joshyorko/rcc/releases)

2. **Configure your OpenAI API key:**
   - Create a `.env` file in the project root
   - Add your API key: `OPENAI_API_KEY=your-api-key-here`
   - Follow the [OpenAI documentation](https://platform.openai.com/docs/quickstart/build-your-application) to generate an API key

## Usage

Run the chat application:
```bash
rcc run -t RunAssistant
```

Run the API key test:
```bash
rcc run -t TestKeys --dev
```

To activate the environment for development:
```bash
rcc ht vars
# or
rcc holotree vars
```

## Configuration

- The default model is `gpt-4.1` - modify in `tasks.py` if needed
- Conversation history is maintained during the session
- Dependencies are managed in `conda.yaml`

## Documentation Resources

* [OpenAI API Documentation](https://platform.openai.com/docs)
* [OpenAI Python Library](https://github.com/openai/openai-python)
* [RCC Documentation](https://github.com/joshyorko/rcc/blob/master/docs/README.md)
* [Robocorp Documentation](https://robocorp.com/docs) - RPA patterns and examples
* [RPA Framework](https://rpaframework.org/) - automation library reference
