/**
 * Example test for Socket context
 */

import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useSocket, SocketProvider, redactSocketDebugArgs } from '../socket';

// Mock socket.io-client - define the mock inline in the factory function
vi.mock('socket.io-client', () => ({
  Manager: class MockManager {
    constructor(url, options) {
      this.url = url;
      this.options = options;
    }

    socket(namespace) {
      const mockSocket = {
        on: vi.fn(),
        off: vi.fn(),
        emit: vi.fn(),
        connect: vi.fn(),
        disconnect: vi.fn(),
        close: vi.fn(),
        onAny: vi.fn(),
        offAny: vi.fn(),
        connected: true,
        id: 'test-socket-id',
        io: {
          on: vi.fn(),
          off: vi.fn(),
          engine: {
            on: vi.fn(),
            off: vi.fn(),
          },
        },
      };
      return mockSocket;
    }
  },
  io: vi.fn(),
}));

describe('SocketProvider', () => {
  it('redacts admin passwords from event-console copies without changing the sent payload', () => {
    const payload = { cmd: 'users.reset_password', data: { user_id: 'user-1', password: 'private' } };
    const args = [payload];

    expect(redactSocketDebugArgs('api.call', args)[0].data.password).toBe('[REDACTED]');
    expect(payload.data.password).toBe('private');
  });

  it('provides socket instance to children', () => {
    const wrapper = ({ children }) => (
      <SocketProvider>{children}</SocketProvider>
    );

    const { result } = renderHook(() => useSocket(), { wrapper });

    expect(result.current.socket).toBeDefined();
  });

  it('socket can emit events', () => {
    const wrapper = ({ children }) => (
      <SocketProvider>{children}</SocketProvider>
    );

    const { result } = renderHook(() => useSocket(), { wrapper });

    // Test that socket has an emit function
    expect(result.current.socket).toBeDefined();
    expect(typeof result.current.socket.emit).toBe('function');

    // Test that emit can be called without errors
    expect(() => {
      result.current.socket.emit('test-event', { data: 'test' });
    }).not.toThrow();
  });
});
