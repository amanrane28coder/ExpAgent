// ExpAgent-generated snippet — flag: checkout-headline, metric event: signup_completed
import flagsmith from '@flagsmith/flagsmith';

await flagsmith.init({
  environmentID: 'MHSyYjVPRHPkaYLkdRHaCS', // toggle-wars Development env key
  api: 'https://edge.api.flagsmith.com/api/v1/',
});

// Read the flag value (variant copy) for this identity:
const value = flagsmith.getValue('checkout-headline');
document.getElementById('cta').textContent = value;

// Record the conversion event when it happens:
flagsmith.track('signup_completed');

