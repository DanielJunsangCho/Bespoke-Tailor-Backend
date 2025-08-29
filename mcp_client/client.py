from typing import Optional
from contextlib import AsyncExitStack
import asyncio
import threading
import time
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()  # load environment variables from .env

class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic()
        self.connected = False

    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server
        
        Args:
            server_script_path: Path to the server script (.py or .js)
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")
            
        command = "python3" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None,
        )
        
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))
        
        await self.session.initialize()
        self.connected = True

    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools"""
        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        available_tools = [{ 
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in response.tools]

        # Loop to handle multiple rounds of tool calls
        url = ''
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            # Make Claude API call
            response = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=messages,
                tools=available_tools
            )

            # Add assistant response to conversation
            messages.append({
                "role": "assistant", 
                "content": response.content
            })
            
            # Check for tool calls
            tool_calls = [content for content in response.content if content.type == 'tool_use']
            
            # If no tool calls, Claude is done
            if not tool_calls:
                break
                
            # Process each tool call
            tool_result_contents = []
            for content in tool_calls:
                tool_name = content.name
                tool_args = content.input
                
                # Execute tool call
                result = await self.session.call_tool(tool_name, tool_args)
                tool_content = result.content[0].text if result.content else str(result)
                
                tool_result_contents.append({
                    "type": "tool_result",
                    "tool_use_id": content.id,
                    "content": tool_content
                })

                if "url" in tool_content: 
                    tool_content = json.loads(tool_content)
                    url = tool_content["url"]
                    
            # Add all tool results as a single user message
            if tool_result_contents:
                messages.append({
                    "role": "user", 
                    "content": tool_result_contents
                })
        return url
    
    async def cleanup(self):
        """Clean up resources"""
        try:
            if self.exit_stack:
                await self.exit_stack.aclose()
        except Exception as e:
            print(f"Error during exit_stack cleanup: {e}")
        finally:
            self.connected = False
            self.session = None


class MCPConnectionPool:
    def __init__(self, server_path: str, pool_size: int = 3):
        self.server_path = server_path
        self.pool_size = pool_size
        self.available = []
        self.in_use = set()
        self.lock = threading.Lock()
        self.initialized = False
        self._event_loop = None
        self._cleanup_done = False
        
    def _run_async(self, coro):
        """Run async coroutine in thread with event loop"""
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_event_loop()
            except RuntimeError:
                self._event_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._event_loop)
        
        return self._event_loop.run_until_complete(coro)
    
    def initialize_pool(self):
        """Initialize the connection pool"""
        if self.initialized:
            return
            
        async def _init():
            for i in range(self.pool_size):
                try:
                    client = MCPClient()
                    await client.connect_to_server(self.server_path)
                    self.available.append(client)
                    print(f"Initialized MCP connection {i+1}/{self.pool_size}")
                except Exception as e:
                    print(f"Failed to initialize MCP connection {i+1}: {e}")
        
        self._run_async(_init())
        self.initialized = True
        print(f"MCP Connection Pool initialized with {len(self.available)} connections")
    
    def get_client(self):
        """Get a client from the pool"""
        with self.lock:
            if not self.available:
                return None
            client = self.available.pop()
            self.in_use.add(client)
            return client
    
    def return_client(self, client):
        """Return a client to the pool"""
        with self.lock:
            if client in self.in_use:
                self.in_use.remove(client)
                if client.connected:
                    self.available.append(client)
                else:
                    # Reconnect if needed
                    try:
                        self._run_async(client.connect_to_server(self.server_path))
                        self.available.append(client)
                    except Exception as e:
                        print(f"Failed to reconnect client: {e}")
    
    def process_resume_request(self, resume_data: str, job_description: str) -> str:
        """Process a resume tailoring request using pooled connection"""
        client = self.get_client()
        if not client:
            return "Error: No available MCP connections. Please try again."
        
        try:
            query = f"""
                You are an expert career coach that has analyzed thousands of resumes for every type of role possible.
                I have a user's resume provided as string data describing their work experience, education, skills, and achievements. I also have a detailed job description for a role they're applying to. Your task is to:

                1. Analyze the user's resume data.
                2. Understand the requirements and focus of the job description.
                3. Select the best LaTeX resume template suited to highlight the user's fit for this job.
                4. Tailor the resume content to fit the chosen template, emphasizing relevant skills and experience.
                5. Compile the tailored LaTeX resume into a PDF document.
                6. Return the compiled PDF as the final response.

                Here is the content of the user's resume:
                {resume_data}

                Here is the job description:
                {job_description}

                Please process this information, generate the LaTeX resume accordingly, and compile it to PDF.
            """
            
            result = self._run_async(client.process_query(query))
            return result
            
        except Exception as e:
            return f"Error processing resume: {str(e)}"
        finally:
            self.return_client(client)
    
    def cleanup_pool(self):
        """Clean up all connections in the pool"""
        if self._cleanup_done:
            return
        
        self._cleanup_done = True
        
        # Clean up clients synchronously to avoid async context issues
        all_clients = list(self.available) + list(self.in_use)
        self.available.clear()
        self.in_use.clear()
        
        # Close event loop and set connected to False for all clients
        for client in all_clients:
            try:
                client.connected = False
                # Close the exit stack if it exists
                if hasattr(client, 'exit_stack') and client.exit_stack:
                    # Mark for cleanup but don't await - let garbage collection handle it
                    pass
            except Exception as e:
                print(f"Error cleaning up client: {e}")
        
        # Close the event loop if we created one
        if self._event_loop and not self._event_loop.is_closed():
            try:
                # Cancel all pending tasks
                pending_tasks = [task for task in asyncio.all_tasks(self._event_loop) 
                               if not task.done()]
                for task in pending_tasks:
                    task.cancel()
                
                # Close the loop
                self._event_loop.close()
            except Exception as e:
                print(f"Error closing event loop: {e}")


if __name__ == "__main__":
    import sys
    # asyncio.run(main())

