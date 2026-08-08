---
id: equations:models
type: equation-registry
title: Формулы демонстрационной модели
---

## Мультимодальное представление

$$
X = \{x_t\}_{t=1}^{T}, \qquad x_t = [v_t; a_t; s_t].
$$

## Слияние модальностей

$$
h_t = \sum_{m \in \{v,a,s\}} \alpha_{t,m} W_m x_{t,m},
\qquad \sum_m \alpha_{t,m}=1.
$$

## Функция потерь

$$
\mathcal{L}(\theta)
= -\sum_{i=1}^{N}\log p_\theta(y_i\mid y_{<i},X)
+ \lambda\,\mathcal{L}_{\mathrm{align}}.
$$

