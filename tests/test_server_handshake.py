"""
Smoke test for MCP server handshake.

This test verifies that the MCP server can handle initialization
and tool listing without crashing, which was broken due to 
notification_options=None being passed to get_capabilities().
"""

import subprocess
import sys
import asyncio
import pytest
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_server_handshake():
    """Test that the MCP server initializes and lists tools correctly."""
    
    # Spawn the server process
    proc = subprocess.Popen(
        [sys.executable, "-m", "ccai_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        # Create client and connect to the server
        async with stdio_client(proc.stdin, proc.stdout) as client:
            # Initialize the server
            init_result = await client.initialize(
                {
                    "serverName": "contact-center-ai",
                    "serverVersion": "0.1.0"
                }
            )
            
            # Verify server info
            assert init_result.server_info.name == "contact-center-ai"
            assert init_result.server_info.version == "0.1.0"
            
            # List tools
            tools_result = await client.list_tools()
            
            # Verify we get the expected tools
            tool_names = [tool.name for tool in tools_result.tools]
            expected_tools = {"search_transcripts", "get_call_summary", "query_csat"}
            assert set(tool_names) == expected_tools
            
            print("Server handshake test passed!")
            return True
            
    except Exception as e:
        print(f"Server handshake test failed: {e}")
        raise
    finally:
        # Clean up the process
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()