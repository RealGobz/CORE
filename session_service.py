"""
Simple in-memory session service for the autonomous routing agent.
Provides session storage and retrieval for ADK Runner.
"""

from typing import Dict, Any, Optional, List
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions import Session
from google.adk import Event


class InMemorySessionService(BaseSessionService):
    """In-memory implementation of session storage for ADK."""
    
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
    
    async def create_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        **kwargs
    ) -> Session:
        """Create a new session and store it in memory."""
        session = Session(
            id=session_id,
            user_id=user_id,
            app_name=app_name,
            events=[]
        )
        self._sessions[session_id] = session
        return session
    
    async def get_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        **kwargs
    ) -> Optional[Session]:
        """Retrieve a session from memory."""
        return self._sessions.get(session_id)
    
    async def update_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        **kwargs
    ) -> Session:
        """Update a session (no-op for in-memory)."""
        return self._sessions.get(session_id)
    
    async def delete_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        **kwargs
    ) -> None:
        """Delete a session from memory."""
        self._sessions.pop(session_id, None)
    
    async def list_sessions(
        self,
        app_name: str,
        user_id: str,
        **kwargs
    ) -> List[Session]:
        """List all sessions for a user/app."""
        return list(self._sessions.values())
