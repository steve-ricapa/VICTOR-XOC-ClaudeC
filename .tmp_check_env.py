import sys
try:
    import claude_agent_sdk
    print('claude_agent_sdk found at', claude_agent_sdk.__file__)
    print('version:', getattr(claude_agent_sdk, '__version__', 'unknown'))
except ImportError:
    print('claude_agent_sdk NOT installed')
import os
for k in ['ANTHROPIC_API_KEY', 'CLAUDE_API_KEY', 'CLAUDE_MODEL', 'CLAUDE_CODE_REFUSAL_DIR']:
    v = os.environ.get(k)
    print(f'{k}={"SET" if v else "(not set)"}')
