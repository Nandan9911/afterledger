import { describe, expect, it } from 'vitest';
import { apiBaseFor, apiUrl, setApiBase } from './api';

describe('the API address', () => {
  it('is read when the page is served, so one build runs anywhere', () => {
    expect(apiUrl('/v1/health')).toBe('http://localhost:8080/v1/health');
    setApiBase('https://afterledger-api-123456789012.asia-south1.run.app/');
    expect(apiUrl('/v1/health')).toBe('https://afterledger-api-123456789012.asia-south1.run.app/v1/health');
    expect(apiUrl('v1/meta')).toBe('https://afterledger-api-123456789012.asia-south1.run.app/v1/meta');
  });

  it('leaves absolute addresses alone', () => {
    expect(apiUrl('https://example.org/x.pdf')).toBe('https://example.org/x.pdf');
  });

  it('on a phone that reached the laptop over Wi-Fi, is the API at the address the page came from', () => {
    expect(apiBaseFor('http://localhost:8080', '192.168.1.23')).toBe('http://192.168.1.23:8080');
    expect(apiBaseFor('http://127.0.0.1:8080/', '10.0.0.7')).toBe('http://10.0.0.7:8080');
    expect(apiBaseFor('http://localhost:8080', 'localhost')).toBe('http://localhost:8080');
    expect(apiBaseFor('http://localhost:8080')).toBe('http://localhost:8080');
    const cloud = 'https://afterledger-api-123456789012.asia-south1.run.app';
    expect(apiBaseFor(cloud, 'afterledger-web-123456789012.asia-south1.run.app')).toBe(cloud);
  });
});

describe('a signed-in browser', () => {
  it('adds its sign-in to the addresses it opens itself, and to nothing else', async () => {
    const { useAuth, safeNext } = await import('./auth');
    setApiBase('https://api.example.org');
    useAuth.getState().signIn('tok.en', { id: 'user_1', name: 'Asha', email: 'a@example.org', role: 'user' });
    expect(apiUrl('/v1/cases/case_1/events')).toBe('https://api.example.org/v1/cases/case_1/events?access_token=tok.en');
    expect(apiUrl('/v1/cases/case_1/letters/l1/file?mirror=1')).toBe(
      'https://api.example.org/v1/cases/case_1/letters/l1/file?mirror=1&access_token=tok.en',
    );
    expect(apiUrl('https://example.org/x.pdf')).toBe('https://example.org/x.pdf');
    useAuth.getState().signOut();
    expect(apiUrl('/v1/cases/case_1/events')).toBe('https://api.example.org/v1/cases/case_1/events');
    expect(safeNext('/new')).toBe('/new');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('https://evil.example')).toBe('/');
  });
});
