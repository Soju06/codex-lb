## 1. Runtime behavior

- [x] 1.1 Move usage refresh work out of `LoadBalancer.select_account()`'s runtime lock without changing account-selection semantics
- [x] 1.2 Keep the serialized in-memory selection/update step so round-robin and runtime error state remain consistent

## 2. Regression coverage

- [x] 2.1 Add a unit test proving runtime mutations are not blocked while refresh is in flight

## 3. Spec updates

- [x] 3.1 Document that slow pre-selection refresh work must not hold the load-balancer runtime lock
