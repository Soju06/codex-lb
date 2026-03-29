import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('error_rate');

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '20s', target: 500 },
    { duration: '1m', target: 500 },
    { duration: '20s', target: 20 },
    { duration: '50s', target: 20 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    error_rate: ['rate<0.10'],
    http_req_duration: ['p(95)<5000'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:2455';

export default function () {
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, { 'health ok': (r) => r.status === 200 });
  errorRate.add(healthRes.status !== 200);

  const readyRes = http.get(`${BASE_URL}/health/ready`);
  check(readyRes, { 'ready ok': (r) => r.status === 200 });
  errorRate.add(readyRes.status !== 200);

  const accountsRes = http.get(`${BASE_URL}/api/accounts`);
  const accountStatusOk = accountsRes.status === 200 || accountsRes.status === 401 || accountsRes.status === 403;
  check(accountsRes, { 'accounts reachable': () => accountStatusOk });
  errorRate.add(!accountStatusOk);

  sleep(1);
}
