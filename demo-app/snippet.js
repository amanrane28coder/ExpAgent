// Produced by: python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks"
// (flag/value pairs use the live Cloud flag checkout-headline: control "Buy now",
//  variant_b "Start free trial". The environment key is the toggle-wars
//  Development SDK key — client-side, public by design.)
// ExpAgent-generated snippet — flag: checkout-headline, metric event: signup_completed
import flagsmith from 'flagsmith';

await flagsmith.init({
  environmentID: 'MHSyYjVPRHPkaYLkdRHaCS', // toggle-wars Development env key
  api: 'https://edge.api.flagsmith.com/api/v1/',
});

// Read the flag value (variant copy) for this identity:
const value = flagsmith.getValue('checkout-headline');
document.getElementById('cta').textContent = value;

// Record the conversion event when it happens:
flagsmith.track('signup_completed');
