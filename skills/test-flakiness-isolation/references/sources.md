# Research References: Flakiness Isolation

- Luo, Q., Hariri, F., Eloussi, L., & Marinov, D. (2014). *An Empirical Analysis of Flaky Tests*. FSE '14. Establishes async wait, concurrency, and test-order dependency as primary root causes of flaky tests and shows why increasing sleep delays fails to eliminate async wait races.
- Eck, M., Palomba, F., Castelluccio, M., & Bacchelli, A. (2019). *Understanding Flaky Tests: The Developer's Perspective*. ESEC/FSE '19. Documents resource leaks, network/port contention, and shared state across parallel workers.
- Lam, W., Oei, R., Shi, A., Marinov, D., & Xie, T. (2019). *iDFlakies: A Framework for Detecting and Partially Classifying Flaky Tests*. ICST '19. Separates order-dependent (`OD`) from non-order-dependent (`NOD`) flaky tests via randomized test-order schedules.
