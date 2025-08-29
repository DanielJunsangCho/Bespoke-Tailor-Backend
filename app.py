from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from mcp_client.client import MCPConnectionPool
import atexit
import os
import time
from collections import defaultdict

app = FastAPI(title="Bespoke Resume Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "chrome-extension:fbcfniofgnkajmaijeogmahmbchncdbl"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize connection pool
server_path = os.path.join(os.path.dirname(__file__), "latex-mcp", "server.py")
mcp_pool = MCPConnectionPool(server_path, pool_size=3)

# Rate limiting storage
request_counts = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # 1 minute
RATE_LIMIT_MAX_REQUESTS = 10  # 10 requests per minute per IP

def check_rate_limit(client_ip: str) -> bool:
    """Check if client IP has exceeded rate limit"""
    now = time.time()
    # Clean old requests
    request_counts[client_ip] = [req_time for req_time in request_counts[client_ip] 
                                if now - req_time < RATE_LIMIT_WINDOW]
    
    # Check if under limit
    if len(request_counts[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    
    # Add current request
    request_counts[client_ip].append(now)
    return True

class ResumeRequest(BaseModel):
    resume_data: str
    job_description: str

@app.post('/api/tailor_resume')
def tailor_resume(request_data: ResumeRequest, request: Request):
    try:
        # Rate limiting check
        client_ip = request.headers.get('X-Forwarded-For', request.client.host)
        if not check_rate_limit(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")
        
        if not request_data.resume_data or not request_data.job_description:
            raise HTTPException(status_code=400, detail="Missing resume_data or job_description")
        
        # Process using connection pool
        result = mcp_pool.process_resume_request(request_data.resume_data, request_data.job_description)
        
        if result.startswith("Error:"):
            raise HTTPException(status_code=503, detail=result)
        
        return {"result": result}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/health')
def health_check():
    """Health check endpoint"""
    pool_status = {
        "available_connections": len(mcp_pool.available),
        "connections_in_use": len(mcp_pool.in_use),
        "pool_initialized": mcp_pool.initialized,
        "total_pool_size": mcp_pool.pool_size
    }
    
    # Check if pool is healthy (has at least 1 available connection)
    is_healthy = mcp_pool.initialized and len(mcp_pool.available) > 0
    status_code = 200 if is_healthy else 503
    
    response_data = {
        "status": "healthy" if is_healthy else "unhealthy", 
        "mcp_pool": pool_status
    }
    
    if not is_healthy:
        raise HTTPException(status_code=503, detail=response_data)
    
    return response_data

@app.post('/health/reconnect')
def force_reconnect():
    """Force reconnect all MCP connections - for debugging"""
    try:
        mcp_pool.cleanup_pool()
        mcp_pool.initialize_pool()
        return {"message": "MCP pool reconnected successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reconnect: {str(e)}")

# Cleanup function
def cleanup_resources():
    """Clean up MCP pool resources"""
    try:
        mcp_pool.cleanup_pool()
        print("MCP pool cleaned up successfully")
    except Exception as e:
        print(f"Error during cleanup: {e}")

# Register cleanup on app shutdown
atexit.register(cleanup_resources)

@app.on_event("startup")
async def startup_event():
    print("Initializing MCP connection pool...")
    # Initialize pool in a thread to avoid event loop conflicts
    import threading
    def init_pool():
        mcp_pool.initialize_pool()
    
    init_thread = threading.Thread(target=init_pool)
    init_thread.start()
    init_thread.join(timeout=60)  

@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down gracefully...")
    cleanup_resources()

if __name__ == '__main__':
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
