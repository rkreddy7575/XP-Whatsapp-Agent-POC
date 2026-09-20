import React, { useState } from 'react';
import { loginWithCredentials } from '../api';

interface LoginModalProps {
  isOpen: boolean;
  onLoginSuccess: () => void;
}

export const LoginModal: React.FC<LoginModalProps> = ({ isOpen, onLoginSuccess }) => {
  const [authMode, setAuthMode] = useState<'password' | 'apikey'>('password');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      if (authMode === 'password') {
        if (!password.trim()) {
          throw new Error('Please enter the dashboard password');
        }
        await loginWithCredentials({ username, password, remember });
      } else {
        if (!apiKey.trim()) {
          throw new Error('Please enter your Owner API Key');
        }
        await loginWithCredentials({ apiKey: apiKey.trim(), remember });
      }
      onLoginSuccess();
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Authentication failed. Please check credentials.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(5, 8, 16, 0.88)',
        zIndex: 2000,
      }}
      id="login-modal-backdrop"
    >
      <div
        className="login-card"
        style={{
          background: 'linear-gradient(180deg, #172036 0%, #0f1523 100%)',
          border: '1px solid rgba(255, 255, 255, 0.12)',
          borderRadius: '16px',
          padding: '2.5rem',
          width: '100%',
          maxWidth: '440px',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.7)',
        }}
        id="login-card"
      >
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div
            style={{
              fontSize: '2.5rem',
              marginBottom: '0.75rem',
              display: 'inline-block',
              background: 'rgba(99, 102, 241, 0.12)',
              padding: '0.75rem',
              borderRadius: '50%',
              border: '1px solid rgba(99, 102, 241, 0.25)',
            }}
          >
            🔐
          </div>
          <h2
            style={{
              fontSize: '1.5rem',
              fontWeight: 800,
              color: 'var(--text-primary)',
              letterSpacing: '-0.025em',
            }}
          >
            MUDHRA OWNER PORTAL
          </h2>
          <p
            style={{
              color: 'var(--text-secondary)',
              fontSize: '0.875rem',
              marginTop: '0.35rem',
            }}
          >
            Single-Tenant Production Pilot • Sign In Required
          </p>
        </div>

        {/* Mode Toggle Tabs */}
        <div
          style={{
            display: 'flex',
            background: 'rgba(10, 14, 26, 0.6)',
            borderRadius: '8px',
            padding: '4px',
            marginBottom: '1.5rem',
            border: '1px solid var(--border-color)',
          }}
        >
          <button
            type="button"
            onClick={() => setAuthMode('password')}
            style={{
              flex: 1,
              padding: '0.5rem',
              background: authMode === 'password' ? 'var(--accent-primary)' : 'transparent',
              color: authMode === 'password' ? '#fff' : 'var(--text-secondary)',
              border: 'none',
              borderRadius: '6px',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            Username & Password
          </button>
          <button
            type="button"
            onClick={() => setAuthMode('apikey')}
            style={{
              flex: 1,
              padding: '0.5rem',
              background: authMode === 'apikey' ? 'var(--accent-primary)' : 'transparent',
              color: authMode === 'apikey' ? '#fff' : 'var(--text-secondary)',
              border: 'none',
              borderRadius: '6px',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            API Key
          </button>
        </div>

        {error && (
          <div
            style={{
              background: 'rgba(244, 63, 94, 0.15)',
              border: '1px solid rgba(244, 63, 94, 0.4)',
              color: '#fda4af',
              padding: '0.75rem 1rem',
              borderRadius: '8px',
              fontSize: '0.875rem',
              marginBottom: '1.25rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}
            id="login-error-message"
          >
            <span>⚠️</span>
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} id="login-form">
          {authMode === 'password' ? (
            <>
              <div style={{ marginBottom: '1.25rem' }}>
                <label
                  htmlFor="login-username"
                  style={{
                    display: 'block',
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    color: 'var(--text-secondary)',
                    marginBottom: '0.5rem',
                  }}
                >
                  Username
                </label>
                <input
                  id="login-username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="admin"
                  required
                  style={{
                    width: '100%',
                    padding: '0.75rem 1rem',
                    background: 'rgba(10, 14, 26, 0.8)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '8px',
                    color: 'var(--text-primary)',
                    fontSize: '0.95rem',
                    outline: 'none',
                  }}
                />
              </div>

              <div style={{ marginBottom: '1.5rem' }}>
                <label
                  htmlFor="login-password"
                  style={{
                    display: 'block',
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    color: 'var(--text-secondary)',
                    marginBottom: '0.5rem',
                  }}
                >
                  Dashboard Password
                </label>
                <input
                  id="login-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  autoFocus
                  required
                  style={{
                    width: '100%',
                    padding: '0.75rem 1rem',
                    background: 'rgba(10, 14, 26, 0.8)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '8px',
                    color: 'var(--text-primary)',
                    fontSize: '0.95rem',
                    outline: 'none',
                  }}
                />
              </div>
            </>
          ) : (
            <div style={{ marginBottom: '1.5rem' }}>
              <label
                htmlFor="login-apikey"
                style={{
                  display: 'block',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  color: 'var(--text-secondary)',
                  marginBottom: '0.5rem',
                }}
              >
                Owner API Key / Secret Token
              </label>
              <input
                id="login-apikey"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Paste DASHBOARD_API_KEY"
                autoFocus
                required
                style={{
                  width: '100%',
                  padding: '0.75rem 1rem',
                  background: 'rgba(10, 14, 26, 0.8)',
                  border: '1px solid var(--border-color)',
                  borderRadius: '8px',
                  color: 'var(--text-primary)',
                  fontSize: '0.95rem',
                  outline: 'none',
                }}
              />
            </div>
          )}

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '1.5rem',
              fontSize: '0.85rem',
              color: 'var(--text-secondary)',
            }}
          >
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                style={{ accentColor: 'var(--accent-primary)', cursor: 'pointer' }}
              />
              Remember on this device
            </label>
          </div>

          <button
            type="submit"
            disabled={loading}
            id="login-submit-btn"
            style={{
              width: '100%',
              padding: '0.85rem',
              background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '8px',
              fontSize: '1rem',
              fontWeight: 700,
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.7 : 1,
              transition: 'opacity 0.2s, transform 0.1s',
            }}
          >
            {loading ? 'Authenticating...' : 'Sign In to Dashboard'}
          </button>
        </form>
      </div>
    </div>
  );
};
