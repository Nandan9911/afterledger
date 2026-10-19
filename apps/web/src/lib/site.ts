'use client';
/**
 * Which copy of AfterLedger this is. The hosted product (AL_SITE=product) shows the product alone: no evaluation pages,
 * no labels about how a sample case was prepared. Any other value is the working copy, with everything. Where
 * sign-in is required (AL_AUTH, on by default on the product), each case belongs to the account that started it.
 */
import { createContext, useContext } from 'react';

type Site = { product: boolean; auth: boolean };
export const SiteContext = createContext<Site>({ product: false, auth: false });
export const useSite = () => useContext(SiteContext);
